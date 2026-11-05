"""Run dnsmasq (DNS, optionally DHCP) and parse its log stream."""
import asyncio
import ipaddress
import logging
import re
import signal
from collections import OrderedDict
from typing import Callable

from . import config as C

log = logging.getLogger("netwatch.dnsmasq")

_QUERY = re.compile(
    r"(\d+) ([0-9a-fA-F.:]+)/\d+ (query\[([A-Z0-9]+)\]|reply|cached) (\S+) (?:from|is) (\S+)"
)
_BLOCKED = re.compile(r"(\d+) ([0-9a-fA-F.:]+)/\d+ \S*adblock\.hosts (\S+) is ")
_DHCPACK = re.compile(r"DHCPACK\([^)]*\) ([\d.]+) ([0-9a-fA-F:]{17})(?: (\S+))?")
_NOISE_SUFFIXES = (".arpa", ".local", ".lan", ".home")


def build_args() -> list[str]:
    args = [
        "dnsmasq", "--keep-in-foreground", "--log-facility=-", "--log-queries=extra",
        "--no-resolv", "--no-hosts", "--bind-interfaces", f"--interface={C.IFACE}",
        "--cache-size=10000", "--domain-needed", "--bogus-priv", "--user=root",
        "--pid-file=",
    ]
    args += [f"--server={s.strip()}" for s in C.DNS_UPSTREAMS if s.strip()]
    # Ad-block list (empty file when the blocker is off; re-read on SIGHUP).
    args.append(f"--addn-hosts={C.DATA}/adblock.hosts")
    from .setup import load_settings
    if load_settings().get("dns_force"):
        # Canary domain: tells Firefox to turn off its built-in DNS-over-HTTPS.
        args.append("--address=/use-application-dns.net/")
    if C.DHCP_ENABLED:
        args += [f"--dhcp-range={a.strip()},{b.strip()},{C.DHCP_LEASE}" for a, b in C.DHCP_RANGES]
        args += [
            "--dhcp-authoritative",
            f"--dhcp-option=option:router,{C.SELF_IP}",
            f"--dhcp-option=option:dns-server,{C.SELF_IP}",
            f"--dhcp-leasefile={C.DATA}/dnsmasq.leases",
            # Per-device overrides (written by the engine, re-read on SIGHUP).
            f"--dhcp-hostsfile={C.DATA}/dhcp-hosts",
            # Bypass devices are told to use the router directly; tagged
            # options win over the untagged router option above.
            f"--dhcp-option=tag:bypass,option:router,{C.ROUTER_IP}",
        ]
    return args


class DnsTracker:
    """Keeps the IP→domain map and emits query / DHCP events."""

    def __init__(self, on_query: Callable[[str, str, str], None],
                 on_dhcp: Callable[[str, str, str | None], None],
                 on_blocked: Callable[[str, str], None] | None = None):
        self.on_query = on_query
        self.on_dhcp = on_dhcp
        self.on_blocked = on_blocked
        self.ip_domain: OrderedDict[str, str] = OrderedDict()
        self._serial: OrderedDict[str, str] = OrderedDict()
        self.proc: asyncio.subprocess.Process | None = None

    def reload(self) -> None:
        """Make dnsmasq re-read the DHCP hosts file."""
        if self.proc and self.proc.returncode is None:
            self.proc.send_signal(signal.SIGHUP)

    def restart(self) -> None:
        """Stop dnsmasq; run_forever starts it again with fresh arguments."""
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()

    def domain_for(self, ip: str) -> str | None:
        return self.ip_domain.get(ip)

    def handle(self, line: str) -> None:
        m = _BLOCKED.search(line)
        if m:
            if self.on_blocked:
                self.on_blocked(m.group(2), m.group(3))
            return
        m = _QUERY.search(line)
        if m:
            serial, client, kind, qtype, name, arg = m.groups()
            if kind.startswith("query"):
                self._remember(self._serial, serial, name, 5000)
                if qtype in ("A", "AAAA", "HTTPS") and not name.endswith(_NOISE_SUFFIXES) and "." in name:
                    self.on_query(client, name, qtype)
            else:
                try:
                    ipaddress.ip_address(arg)
                except ValueError:
                    return  # <CNAME>, NXDOMAIN, NODATA...
                # Attribute the IP to the name the client originally asked for.
                self._remember(self.ip_domain, arg, self._serial.get(serial, name), 50000)
            return
        m = _DHCPACK.search(line)
        if m:
            ip, mac, host = m.groups()
            self.on_dhcp(ip, mac.lower(), host)

    @staticmethod
    def _remember(d: OrderedDict, key: str, value: str, cap: int) -> None:
        d[key] = value
        d.move_to_end(key)
        while len(d) > cap:
            d.popitem(last=False)


async def run_forever(tracker: DnsTracker) -> None:
    backoff = 1
    while True:
        args = build_args()
        log.info("starting dnsmasq (dhcp=%s)", C.DHCP_ENABLED)
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        tracker.proc = proc
        try:
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                try:
                    tracker.handle(line)
                except Exception:
                    log.exception("bad dnsmasq line: %s", line)
                if not any(k in line for k in ("query[", " reply ", " cached ", " forwarded ", " config ", "adblock.hosts ")):
                    log.info("%s", line)
                backoff = 1
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            await proc.wait()
        log.warning("dnsmasq exited with %s; restarting in %ss", proc.returncode, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)
