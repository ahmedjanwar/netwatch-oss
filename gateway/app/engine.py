"""The core: ties discovery, conntrack, DNS and storage into one live model."""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C
import re

from . import conntrack, discovery, netfilter, services
from .dnslog import DnsTracker, run_forever as run_dnsmasq
from .store import Store

log = logging.getLogger("netwatch.engine")


@dataclass
class Device:
    mac: str
    ip: str = ""
    hostname: str = ""
    name: str = ""
    vendor: str = ""
    first_seen: float = 0
    last_seen: float = 0
    blocked: bool = False
    bypass: bool = False
    approved: bool = True
    paused_until: float = 0
    is_self: bool = False
    # live
    up_rate: float = 0
    down_rate: float = 0
    last_routed: float = 0
    flows: list = field(default_factory=list)
    flow_count: int = 0
    ptr_tried: float = 0

    def row(self) -> tuple:
        return (self.mac, self.ip, self.hostname, self.name, self.vendor,
                self.first_seen, self.last_seen, int(self.blocked), int(self.bypass),
                int(self.approved), self.paused_until)


def _midnight() -> float:
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


class Engine:
    def __init__(self) -> None:
        Path(C.DATA).mkdir(parents=True, exist_ok=True)
        self.store = Store(f"{C.DATA}/netwatch.db")
        self.devices: dict[str, Device] = {}
        self.ip2mac: dict[str, str] = {}
        self.clients: set = set()
        self.tasks: list[asyncio.Task] = []
        self.dns = DnsTracker(self._on_query, self._on_dhcp, self._on_blocked)
        self.adblock = None  # AdBlocker, attached by main
        self._device_dns: deque = deque(maxlen=5000)  # timestamps of DNS queries from LAN devices
        self.ipv6_ra = False
        self.started = time.time()
        self.ads_today: dict[str, int] = defaultdict(int)
        self._ads_acc: dict[tuple[str, str], int] = defaultdict(int)
        self.self_mac = Path(f"/sys/class/net/{C.IFACE}/address").read_text().strip().lower()
        self.router_mac = ""
        self.events: deque = deque(maxlen=60)
        self.dns_feed: deque = deque(maxlen=60)
        self._dns_recent: dict[tuple[str, str], float] = {}
        self._domain_acc: dict[tuple[str, str], list] = {}
        self.history: deque = deque(maxlen=300)  # (ts, up B/s, down B/s)
        self.totals = (0.0, 0.0)
        self._prev_flows: dict[str, tuple[int, int]] = {}
        self._last_poll: float | None = None
        self._minute_acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self.today: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self._today_start = _midnight()
        self._dirty = False
        self._quiet_until = 0.0
        self.snapshot_json = "{}"
        self.bot = None  # TelegramBot, attached by main
        self._svc_acc: dict[tuple[str, str], int] = defaultdict(int)
        self._tp_peak = 0.0
        self._tp_sum = 0.0
        self._tp_n = 0
        self._net_fails = 0
        self.outage_start: float | None = None
        self.require_approval = False

    # ---------- lifecycle ----------
    async def start(self) -> None:
        discovery.load_oui()
        rows = self.store.load_devices()
        for r in rows:
            self.devices[r["mac"]] = Device(
                mac=r["mac"], ip=r["ip"] or "", hostname=r["hostname"] or "",
                name=r["name"] or "", vendor=r["vendor"] or discovery.vendor_of(r["mac"]),
                first_seen=r["first_seen"] or 0,
                last_seen=r["last_seen"] or 0, blocked=bool(r["blocked"]),
                bypass=bool(r["bypass"]), approved=bool(r["approved"]),
                paused_until=r["paused_until"] or 0)
            if r["ip"]:
                self.ip2mac[r["ip"]] = r["mac"]
        if not rows:
            # First run: don't spam "new device" alerts for what's already here.
            self._quiet_until = time.time() + 180
        me = self._device(self.self_mac, C.SELF_IP)
        me.is_self = True
        if not me.name:
            me.name = "Netwatch Gateway"
        for e in self.store.recent_events(60):
            self.events.append(e)
        for mac, v in self.store.totals_since(self._today_start).items():
            self.today[mac] = v

        self.settings_changed()
        self._write_dhcp_hosts()
        for mac, n in self.store.adblock_by_mac(time.strftime("%Y-%m-%d")).items():
            self.ads_today[mac] = n
        from .setup import load_settings
        try:
            netfilter.setup(sorted(self._drop_set()), force_dns=bool(load_settings().get("dns_force")))
        except Exception as e:
            # Keep DNS/DHCP alive even if the firewall can't be configured:
            # the house loses blocking/NAT extras, not its internet.
            log.exception("firewall setup failed")
            self.event("error", f"Firewall setup failed, running without blocking: {e}")
        for coro in (run_dnsmasq(self.dns), self._poll_loop(), self._scan_loop(),
                     self._neigh_loop(), self._name_loop(), self._flush_loop(), self._health_loop(),
                     self._pause_loop()):
            self.tasks.append(asyncio.create_task(coro))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self._flush()
        netfilter.teardown()

    # ---------- device model ----------
    def _device(self, mac: str, ip: str = "") -> Device:
        d = self.devices.get(mac)
        now = time.time()
        if d is None:
            d = Device(mac=mac, ip=ip, vendor=discovery.vendor_of(mac), first_seen=now, last_seen=now)
            self.devices[mac] = d
            self._dirty = True
            if mac != self.self_mac:
                self._new_device(d, ip, alert=now > self._quiet_until)
        if ip and d.ip != ip:
            if d.ip and self.ip2mac.get(d.ip) == mac:
                del self.ip2mac[d.ip]
            d.ip = ip
            self._dirty = True
        if ip:
            self.ip2mac[ip] = mac
        return d

    def _new_device(self, d: Device, ip: str, alert: bool) -> None:
        what = f"{d.vendor or 'unknown vendor'} ({ip})"
        if alert and self.require_approval:
            d.approved = False
            self._sync_drop()
            self._event("pending", d.mac, f"New device waiting for approval: {what}")
            if self.bot:
                # Give DHCP a moment to report the hostname before notifying.
                asyncio.get_running_loop().call_later(4, self.bot.device_pending, d)
        else:
            self._event("new", d.mac, f"New device joined: {what}", alert=alert)
            if alert and self.bot:
                asyncio.get_running_loop().call_later(4, self.bot.device_joined, d)

    def settings_changed(self) -> None:
        from .setup import load_settings
        self.require_approval = bool(load_settings().get("require_approval", False))

    def _drop_set(self) -> set[str]:
        now = time.time()
        return {d.mac for d in self.devices.values()
                if d.blocked or not d.approved or d.paused_until > now}

    def event(self, kind: str, message: str) -> None:
        self._event(kind, self.self_mac, message)

    # ---------- pause timers ----------
    def pause(self, mac: str, until: float, via: str = "dashboard") -> Device:
        d = self.devices[mac]
        if d.is_self:
            raise ValueError("Can't pause the gateway")
        if d.bypass:
            raise ValueError("Turn off full speed first: its traffic doesn't pass through Netwatch")
        d.paused_until = until
        d.approved = True
        self._sync_drop([d.ip])
        when = time.strftime("%H:%M", time.localtime(until))
        self._event("pause", mac, f"{self.label(d)} paused until {when} (via {via})")
        self._flush_devices()
        self._build_snapshot()
        return d

    def resume(self, mac: str, via: str = "dashboard", expired: bool = False) -> Device:
        d = self.devices[mac]
        if not d.paused_until:
            return d
        d.paused_until = 0
        self._sync_drop()
        self._event("unblock", mac, f"{self.label(d)}: pause {'ended' if expired else 'cancelled'}"
                    + ("" if expired else f" (via {via})"))
        if self.bot:
            self.bot.device_resumed(d, expired)
        self._flush_devices()
        self._build_snapshot()
        return d

    async def _pause_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            now = time.time()
            for d in list(self.devices.values()):
                if d.paused_until and d.paused_until <= now:
                    try:
                        self.resume(d.mac, expired=True)
                    except Exception:
                        log.exception("resume failed")

    def _sync_drop(self, kill_ips: list[str] = ()) -> None:
        netfilter.sync_drop(self._drop_set(), [ip for ip in kill_ips if ip])

    def decide(self, mac: str, verdict: str, via: str = "dashboard") -> Device:
        """Resolve a pending device: allow, allow_bypass or block."""
        d = self.devices[mac]
        if d.is_self:
            raise ValueError("That's the gateway")
        d.approved = True
        d.paused_until = 0
        if verdict == "block":
            d.blocked, d.bypass = True, False
            text = "🚫 Blocked"
        elif verdict == "allow_bypass":
            d.blocked, d.bypass = False, True
            text = "⚡ Allowed at full speed"
        elif verdict == "allow":
            d.blocked = False
            text = "✅ Allowed"
        else:
            raise ValueError(f"unknown verdict {verdict}")
        self._write_dhcp_hosts()
        self.dns.reload()
        self._sync_drop([d.ip] if d.blocked else [])
        self._event("block" if d.blocked else "unblock", mac,
                    f"{self.label(d)}: {text.split(' ', 1)[1].lower()} (via {via})")
        if self.bot:
            self.bot.decision_made(d, text, via)
        self._flush_devices()
        self._build_snapshot()
        return d

    def seen(self, ip: str, mac: str, hostname: str | None = None) -> None:
        if ip == C.ROUTER_IP:
            self.router_mac = mac
            return
        if not discovery.valid_mac(mac) or ip == C.SELF_IP and mac != self.self_mac:
            return
        d = self._device(mac, ip)
        d.last_seen = time.time()
        if hostname and hostname != "*" and hostname != d.hostname:
            d.hostname = hostname
            self._dirty = True

    def _event(self, kind: str, mac: str, message: str, alert: bool = True) -> None:
        ts = time.time()
        if alert:
            e = {"ts": ts, "kind": kind, "mac": mac, "message": message}
            self.events.append(e)
            self.store.add_event(ts, kind, mac, message)
        log.info("%s: %s", kind, message)

    # ---------- DNS / DHCP callbacks ----------
    def _on_query(self, client: str, domain: str, qtype: str) -> None:
        mac = self.self_mac if client in ("127.0.0.1", C.SELF_IP) else self.ip2mac.get(client)
        if not mac:
            return
        now = time.time()
        d = self.devices.get(mac)
        if d and not d.is_self:
            d.last_seen = now
            self._device_dns.append(now)
        key = (mac, domain)
        acc = self._domain_acc.setdefault(key, [0, now])
        acc[0] += 1
        acc[1] = now
        # The live feed shows each device+domain at most once per 30 s.
        if now - self._dns_recent.get(key, 0) > 30:
            self._dns_recent[key] = now
            self.dns_feed.append({"ts": now, "mac": mac, "domain": domain, "type": qtype})
            if len(self._dns_recent) > 20000:
                cutoff = now - 30
                self._dns_recent = {k: v for k, v in self._dns_recent.items() if v > cutoff}

    def _on_blocked(self, client: str, domain: str) -> None:
        mac = self.self_mac if client in ("127.0.0.1", C.SELF_IP) else self.ip2mac.get(client)
        if not mac:
            return
        self.ads_today[mac] += 1
        self._ads_acc[(mac, domain)] += 1
        now = time.time()
        key = (mac, "blocked:" + domain)
        if now - self._dns_recent.get(key, 0) > 30:
            self._dns_recent[key] = now
            self.dns_feed.append({"ts": now, "mac": mac, "domain": domain, "type": "A", "blocked": True})

    def _on_dhcp(self, ip: str, mac: str, hostname: str | None) -> None:
        self.seen(ip, mac, hostname)

    # ---------- user actions ----------
    def update_device(self, mac: str, name: str | None, blocked: bool | None,
                      bypass: bool | None = None, approved: bool | None = None) -> Device:
        d = self.devices[mac]
        if name is not None:
            d.name = name.strip()[:40]
        if approved and not d.approved:
            self.decide(mac, "allow")
        if bypass is not None and bypass != d.bypass:
            if d.is_self or mac == self.router_mac:
                raise ValueError("The gateway itself can't bypass")
            if bypass and d.blocked:
                raise ValueError("Unblock this device first: bypassed devices can't be blocked")
            if bypass and not d.approved:
                raise ValueError("Approve this device first")
            if bypass and d.paused_until > time.time():
                raise ValueError("Resume this device first: it's paused")
            d.bypass = bypass
            self._write_dhcp_hosts()
            self.dns.reload()
            self._event("bypass", mac, f"{self.label(d)} " + (
                "set to full speed (bypasses Netwatch)" if bypass else "routed through Netwatch again"))
        if blocked is not None and blocked != d.blocked:
            if d.is_self or mac == self.router_mac:
                raise ValueError("cannot block the gateway or router")
            if blocked and d.bypass:
                raise ValueError("Turn off full-speed bypass first: its traffic doesn't pass through Netwatch")
            d.blocked = blocked
            d.approved = True
            d.paused_until = 0
            self._sync_drop([d.ip] if blocked else [])
            if self.bot:
                self.bot.decision_made(d, "🚫 Blocked" if blocked else "✅ Allowed", "dashboard")
            self._event("block" if blocked else "unblock", mac,
                        f"{self.label(d)} {'blocked from' if blocked else 'allowed on'} the internet")
        self._dirty = True
        self._flush_devices()
        return d

    def _write_dhcp_hosts(self) -> None:
        lines = [f"{d.mac},set:bypass" for d in self.devices.values() if d.bypass and d.approved]
        path = Path(C.DATA) / "dhcp-hosts"
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(f"{line}\n" for line in lines))
        tmp.replace(path)

    def set_gateway_mode(self, enabled: bool) -> None:
        from .setup import save_settings
        save_settings(dhcp=enabled)
        if C.DHCP_ENABLED != enabled:
            C.DHCP_ENABLED = enabled
            self.dns.restart()
            self._event("mode", self.self_mac,
                        "Gateway mode ON: now serving DHCP" if enabled else "Gateway mode OFF: monitor only")
        self._build_snapshot()

    def label(self, d: Device) -> str:
        return d.name or d.hostname or d.vendor or d.ip or d.mac

    # ---------- loops ----------
    async def _poll_loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                out = await discovery.run_cmd("conntrack", "-L", "-f", "ipv4", "-o", "id")
                self._process_flows(out)
            except Exception:
                log.exception("flow poll failed")
            self._build_snapshot()
            await self._broadcast()
            await asyncio.sleep(max(0.2, C.POLL_SECONDS - (time.monotonic() - started)))

    def _process_flows(self, out: str) -> None:
        now_m = time.monotonic()
        dt = (now_m - self._last_poll) if self._last_poll else None
        first = self._last_poll is None
        self._last_poll = now_m
        now = time.time()
        if now - self._today_start >= 86400:
            self._today_start = _midnight()
            self.today.clear()
            self.ads_today.clear()

        current: dict[str, tuple[int, int]] = {}
        per_owner: dict[str, list] = defaultdict(list)
        for line in out.splitlines():
            f = conntrack.parse_line(line)
            if f is None:
                continue
            owner = conntrack.owner_of(f, C.LAN, C.SELF_IP)
            if owner is None:
                continue
            prev = self._prev_flows.get(f.fid)
            if prev is None:
                dup, ddown = (0, 0) if first else (f.up, f.down)
            else:
                dup, ddown = max(0, f.up - prev[0]), max(0, f.down - prev[1])
            current[f.fid] = (f.up, f.down)
            per_owner[owner].append((f, dup, ddown))
        self._prev_flows = current
        if not dt:
            return

        tot_up = tot_down = 0.0
        for d in self.devices.values():
            d.up_rate = d.down_rate = 0
            d.flows = []
            d.flow_count = 0
        for ip, flows in per_owner.items():
            mac = self.self_mac if ip == C.SELF_IP else self.ip2mac.get(ip)
            d = self.devices.get(mac) if mac else None
            if d is None:
                continue
            up = sum(x[1] for x in flows)
            down = sum(x[2] for x in flows)
            d.up_rate, d.down_rate = up / dt, down / dt
            tot_up += d.up_rate
            tot_down += d.down_rate
            if not d.is_self:
                d.last_routed = now
                d.last_seen = now
            acc = self._minute_acc[d.mac]
            acc[0] += up
            acc[1] += down
            t = self.today[d.mac]
            t[0] += up
            t[1] += down
            for f, du, dd in flows:
                if du or dd:
                    self._svc_acc[(d.mac, services.classify(self.dns.domain_for(f.dst)))] += du + dd
            flows.sort(key=lambda x: (x[1] + x[2], x[0].up + x[0].down), reverse=True)
            d.flows = [{
                "proto": f.proto, "remote": f.dst, "port": f.dport,
                "domain": self.dns.domain_for(f.dst),
                "up": f.up, "down": f.down, "up_rate": du / dt, "down_rate": dd / dt,
            } for f, du, dd in flows[:30]]
            d.flow_count = len(flows)
        self.totals = (tot_up, tot_down)
        combined = tot_up + tot_down  # both directions share this machine's network link
        self._tp_peak = max(self._tp_peak, combined)
        self._tp_sum += combined
        self._tp_n += 1
        self.history.append((round(now, 1), round(tot_up), round(tot_down)))

    async def _scan_loop(self) -> None:
        while True:
            try:
                for ip, mac in await discovery.arp_scan():
                    self.seen(ip, mac)
            except Exception:
                log.exception("arp scan failed")
            await asyncio.sleep(C.ARP_SCAN_EVERY)

    async def _neigh_loop(self) -> None:
        while True:
            try:
                for ip, mac, state in await discovery.neighbours():
                    if ip == C.ROUTER_IP:
                        self.router_mac = mac
                    elif mac in self.devices or state == "REACHABLE":
                        if state == "REACHABLE":
                            self.seen(ip, mac)
                        else:
                            self._device(mac, ip)
            except Exception:
                log.exception("neighbour read failed")
            await asyncio.sleep(5)

    async def _name_loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            now = time.time()
            for d in list(self.devices.values()):
                if d.is_self or d.hostname or not d.ip or now - d.ptr_tried < 1800:
                    continue
                d.ptr_tried = now
                name = await asyncio.to_thread(discovery.ptr_lookup, d.ip)
                if name:
                    d.hostname = name
                    self._dirty = True
            await asyncio.sleep(20)

    async def _ping(self, host: str) -> tuple[float | None, float]:
        out = await discovery.run_cmd("ping", "-n", "-q", "-c", "4", "-i", "0.3", "-W", "1", host, timeout=10)
        loss = re.search(r"([\d.]+)% packet loss", out)
        rtt = re.search(r"= [\d.]+/([\d.]+)/", out)
        return (float(rtt.group(1)) if rtt else None), (float(loss.group(1)) if loss else 100.0)

    async def _health_loop(self) -> None:
        await asyncio.sleep(10)
        while True:
            try:
                (net_rtt, net_loss), (rt_rtt, rt_loss) = await asyncio.gather(
                    self._ping("1.1.1.1"), self._ping(C.ROUTER_IP))
                now = time.time()
                self.store.add_health(now, "internet", net_rtt, net_loss)
                self.store.add_health(now, "router", rt_rtt, rt_loss)
                self._track_outage(now, net_loss >= 100, rt_loss >= 100)
                from .setup import ipv6_router_advertising
                self.ipv6_ra = await ipv6_router_advertising()
            except Exception:
                log.exception("health check failed")
            await asyncio.sleep(30)

    def router_vendor(self) -> str:
        """Who made the router, from its MAC (e.g. "TP-Link"), or "" if we can't tell."""
        mac = self.router_mac or discovery.mac_of(C.ROUTER_IP)
        return discovery.short_vendor(discovery.vendor_of(mac)) if mac else ""

    def router_name(self) -> str:
        """What the user called the router in setup, else "<Vendor> router", else "Router"."""
        if C.ROUTER_NAME:
            return C.ROUTER_NAME
        vendor = self.router_vendor()
        return f"{vendor} router" if vendor else "Router"

    def _track_outage(self, now: float, net_down: bool, router_down: bool) -> None:
        self._net_fails = self._net_fails + 1 if net_down else 0
        if self._net_fails == 2 and self.outage_start is None:
            self.outage_start = now - 30
            cause = f"{self.router_name()} isn't responding" if router_down else \
                f"{self.router_name()} is fine, so it's likely your internet provider"
            self._event("outage", self.self_mac, f"Internet down: {cause}")
        elif not net_down and self.outage_start is not None:
            mins = max(1, round((now - self.outage_start) / 60))
            start = time.strftime("%H:%M", time.localtime(self.outage_start))
            self._event("outage", self.self_mac, f"Internet back after {mins} min (down since {start})")
            if self.bot:
                # Sent on recovery: Telegram is unreachable while the internet is down.
                self.bot.outage_recovered(f"📶 <b>Internet is back</b>\nIt was down for about {mins} min, since {start}.")
            self.outage_start = None

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(60 - time.time() % 60)
            try:
                self._flush()
                if int(time.time()) % 86400 < 60:
                    self.store.prune()
            except Exception:
                log.exception("flush failed")

    def _flush(self) -> None:
        minute = int(time.time() // 60 * 60) - 60
        if self._minute_acc:
            self.store.add_usage(minute, dict(self._minute_acc))
            self._minute_acc.clear()
        if self._domain_acc:
            self.store.add_domains({k: (v[0], v[1]) for k, v in self._domain_acc.items()})
            self._domain_acc.clear()
        if self._ads_acc:
            self.store.add_adblock(time.strftime("%Y-%m-%d", time.localtime(minute)), dict(self._ads_acc))
            self._ads_acc.clear()
        if self._svc_acc:
            self.store.add_service_usage(time.strftime("%Y-%m-%d", time.localtime(minute)), dict(self._svc_acc))
            self._svc_acc.clear()
        if self._tp_n:
            self.store.add_throughput(minute, self._tp_peak, self._tp_sum / self._tp_n)
            self._tp_peak = self._tp_sum = 0.0
            self._tp_n = 0
        self._flush_devices()

    def _flush_devices(self) -> None:
        # last_seen changes constantly; persist every flush regardless of dirtiness.
        self.store.save_devices([d.row() for d in self.devices.values()])
        self._dirty = False

    # ---------- snapshot / websocket ----------
    def device_json(self, d: Device, now: float) -> dict:
        online = d.is_self or now - d.last_seen < C.ONLINE_WINDOW
        today = self.today.get(d.mac, [0, 0])
        return {
            "mac": d.mac, "ip": d.ip, "name": d.name, "hostname": d.hostname,
            "label": self.label(d), "vendor": d.vendor,
            "type": "gateway" if d.is_self else discovery.guess_type(d.vendor, d.hostname, d.name),
            "online": online, "blocked": d.blocked, "self": d.is_self,
            "bypass": d.bypass, "approved": d.approved,
            "paused_until": d.paused_until if d.paused_until > now else 0,
            "ads_today": self.ads_today.get(d.mac, 0),
            "routed": d.is_self or now - d.last_routed < 120,
            "first_seen": d.first_seen, "last_seen": d.last_seen,
            "up": round(d.up_rate), "down": round(d.down_rate),
            "today_up": today[0], "today_down": today[1],
            "flows": d.flows, "flow_count": d.flow_count,
        }

    def _build_snapshot(self) -> None:
        now = time.time()
        devices = [self.device_json(d, now) for d in self.devices.values()]
        self.snapshot_json = json.dumps({
            "ts": now,
            "mode": "gateway" if C.DHCP_ENABLED else "monitor",
            "iface": C.IFACE, "self_ip": C.SELF_IP,
            "router": {"ip": C.ROUTER_IP, "mac": self.router_mac, "name": self.router_name()},
            "totals": {"up": round(self.totals[0]), "down": round(self.totals[1])},
            "devices": devices,
            "dns": list(self.dns_feed),
            "events": list(self.events),
            "history": list(self.history),
            "uptime": round(now - self.started),
            "dns_coverage": {
                "device_queries_10m": sum(1 for t in self._device_dns if now - t < 600),
                "ipv6_ra": self.ipv6_ra,
            },
            "adblock": {
                "enabled": bool(self.adblock and self.adblock.enabled),
                "blocked_today": sum(self.ads_today.values()),
            },
        }, separators=(",", ":"))

    async def _broadcast(self) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(self.snapshot_json), 5)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
