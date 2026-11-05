"""Netwatch Telegram bot: a remote control for the home network.

- Persistent button menu (no commands to remember)
- Smart new-device alerts that wait for the device's real name
- Tappable device cards: block / pause / full speed / rename, edited in place
- Undo on every decision, chart images in reports, settings toggles

Only the paired chat (one-time code from the Settings page) can use it.
"""
import asyncio
import html
import json
import logging
import secrets
import time
import urllib.error
import urllib.request
import uuid

from . import config as C
from .discovery import guess_type
from .setup import load_settings, save_settings

log = logging.getLogger("netwatch.telegram")
TOKEN = C.TELEGRAM_TOKEN
API = f"https://api.telegram.org/bot{TOKEN}/"
e = html.escape

ICON = {"gateway": "🛡️", "phone": "📱", "tablet": "📱", "watch": "⌚", "computer": "💻", "tv": "📺",
        "console": "🎮", "speaker": "🔊", "iot": "💡", "unknown": "📡"}
KIND = {"phone": "phone", "tablet": "tablet", "watch": "smartwatch", "computer": "computer", "tv": "TV",
        "console": "game console", "speaker": "smart speaker", "iot": "smart-home device", "unknown": "device"}

# Reply keyboard: the menu pinned under the message box.
B_STATUS, B_DEVICES, B_PENDING, B_BLOCKED, B_REPORT, B_SETTINGS = (
    "📡 Status", "📱 Devices", "⏳ Pending", "🚫 Blocked", "📊 Report", "⚙️ Settings")
MENU_BUTTONS = (B_STATUS, B_DEVICES, B_PENDING, B_BLOCKED, B_REPORT, B_SETTINGS)
MENU = {"keyboard": [[{"text": B_STATUS}, {"text": B_DEVICES}],
                     [{"text": B_PENDING}, {"text": B_BLOCKED}],
                     [{"text": B_REPORT}, {"text": B_SETTINGS}]],
        "resize_keyboard": True, "is_persistent": True}
PAGE = 8


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_rate(bps: float) -> str:
    b = bps * 8
    return f"{b / 1e3:.0f} Kb/s" if b < 1e6 else f"{b / 1e6:.1f} Mb/s"


def hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def next_morning(hour: int = 7) -> float:
    t = time.localtime()
    ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, 0, 0, 0, 0, -1))
    return ts if ts > time.time() + 60 else ts + 86400


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


class TelegramBot:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.adblock = None
        self.username = ""
        self._pair_code: tuple[str, float] | None = None
        self._sent_times: list[float] = []
        self._alert_msgs: dict[str, int] = {}   # mac -> message showing the approval/result
        self._alerting: set[str] = set()
        self._rename: tuple[str, int | None, float] | None = None  # (mac, card msg id, deadline)
        self._offset = 0

    # ================= state =================
    @property
    def configured(self) -> bool:
        return bool(TOKEN)

    @property
    def chat_id(self) -> int | None:
        return load_settings().get("telegram_chat")

    @staticmethod
    def _opt(key: str, default: bool = True) -> bool:
        return bool(load_settings().get(key, default))

    def status(self) -> dict:
        s = load_settings()
        return {"configured": self.configured, "bot_username": self.username,
                "paired": bool(s.get("telegram_chat")), "chat_name": s.get("telegram_chat_name", ""),
                "require_approval": bool(s.get("require_approval", False))}

    def new_pair_code(self) -> dict:
        code = f"{secrets.randbelow(10**6):06d}"
        self._pair_code = (code, time.time() + 600)
        return {"code": code, "bot_username": self.username, "expires_in": 600}

    def unpair(self) -> None:
        save_settings(telegram_chat=None, telegram_chat_name="", require_approval=False)
        self.engine.settings_changed()

    # ================= Telegram API =================
    def _call(self, method: str, http_timeout: float = 15, **params) -> dict:
        req = urllib.request.Request(API + method, data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"})
        return self._do(req, method, http_timeout)

    @staticmethod
    def _do(req, method: str, http_timeout: float) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as err:
            body = json.loads(err.read() or b"{}")
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method}: {body.get('description', 'error')}")
        return body["result"]

    def _upload_photo(self, chat: int, png: bytes, caption: str, markup: dict | None) -> dict:
        boundary = uuid.uuid4().hex
        fields = {"chat_id": str(chat), "caption": caption, "parse_mode": "HTML"}
        if markup:
            fields["reply_markup"] = json.dumps(markup)
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="report.png"\r\n'
                     f"Content-Type: image/png\r\n\r\n".encode() + png + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(API + "sendPhoto", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return self._do(req, "sendPhoto", 30)

    async def call(self, method: str, http_timeout: float = 15, **params) -> dict:
        return await asyncio.to_thread(self._call, method, http_timeout, **params)

    def _rate_ok(self) -> bool:
        now = time.time()
        self._sent_times = [t for t in self._sent_times if now - t < 3600]
        if len(self._sent_times) >= 120:
            log.warning("telegram rate cap reached; dropping message")
            return False
        self._sent_times.append(now)
        return True

    async def send(self, text: str, buttons: list[list[dict]] | None = None,
                   markup: dict | None = None) -> int | None:
        chat = self.chat_id
        if not (self.configured and chat) or not self._rate_ok():
            return None
        params = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons is not None:
            params["reply_markup"] = {"inline_keyboard": buttons}
        elif markup:
            params["reply_markup"] = markup
        try:
            return (await self.call("sendMessage", **params))["message_id"]
        except Exception as err:
            log.warning("telegram send failed: %s", err)
            return None

    async def edit(self, mid: int, text: str, buttons: list[list[dict]] | None = None) -> bool:
        try:
            await self.call("editMessageText", chat_id=self.chat_id, message_id=mid, text=text,
                            parse_mode="HTML", disable_web_page_preview=True,
                            reply_markup={"inline_keyboard": buttons or []})
            return True
        except RuntimeError as err:
            if "not modified" in str(err):
                return True
            log.info("telegram edit failed: %s", err)
            return False

    async def show(self, mid: int | None, text: str, buttons) -> None:
        """Edit the message in place if we can, else send a new one."""
        if not (mid and await self.edit(mid, text, buttons)):
            await self.send(text, buttons)

    async def send_photo(self, png: bytes, caption: str, buttons=None) -> None:
        if not (self.configured and self.chat_id) or not self._rate_ok():
            return
        markup = {"inline_keyboard": buttons} if buttons else None
        try:
            await asyncio.to_thread(self._upload_photo, self.chat_id, png, caption[:1024], markup)
        except Exception as err:
            log.warning("telegram photo failed: %s", err)
            await self.send(caption, buttons)

    def notify(self, text: str, buttons=None) -> None:
        """Fire-and-forget from synchronous engine code."""
        if self.configured and self.chat_id:
            asyncio.get_running_loop().create_task(self.send(text, buttons))

    # ================= descriptions =================
    def _type(self, d) -> str:
        return "gateway" if d.is_self else guess_type(d.vendor, d.hostname, d.name)

    def _icon(self, d) -> str:
        return ICON.get(self._type(d), ICON["unknown"])

    def _state(self, d) -> tuple[str, str]:
        now = time.time()
        if not d.approved:
            return "⏳", "waiting for approval · no internet"
        if d.blocked:
            return "🚫", "blocked from the internet"
        if d.paused_until > now:
            return "⏸", f"paused until {hhmm(d.paused_until)}"
        online = d.is_self or now - d.last_seen < C.ONLINE_WINDOW
        if d.bypass:
            return ("⚡" if online else "⚪"), "full speed (bypasses Netwatch)" + ("" if online else " · offline")
        if online:
            return "🟢", "online"
        return "⚪", f"offline · last seen {self._ago(d.last_seen)}"

    @staticmethod
    def _ago(ts: float) -> str:
        s = time.time() - ts
        if s < 3600:
            return f"{max(1, int(s // 60))} min ago"
        if s < 86400:
            return f"{int(s // 3600)} h ago"
        return f"{int(s // 86400)} d ago"

    @staticmethod
    def _maker(d) -> str:
        if d.vendor == "Private MAC":
            return "private Wi-Fi address (normal for phones, tablets and watches)"
        return d.vendor or "unknown maker"

    def _twin(self, d):
        """An approved device with the same hostname: likely the same gadget with a new private address."""
        if not d.hostname:
            return None
        for o in self.engine.devices.values():
            if o.mac != d.mac and o.approved and o.hostname and o.hostname.lower() == d.hostname.lower():
                return o
        return None

    def _first_domains(self, mac: str, n: int = 4) -> list[str]:
        seen: list[str] = []
        for q in self.engine.dns_feed:
            if q["mac"] == mac and q["domain"] not in seen:
                seen.append(q["domain"])
        return seen[:n]

    # ================= engine hooks =================
    def device_pending(self, d) -> None:
        if not (self.configured and self.chat_id) or d.approved or d.mac in self._alerting \
                or d.mac in self._alert_msgs:
            return
        self._alerting.add(d.mac)
        asyncio.get_running_loop().create_task(self._alert_pending(d))

    async def _alert_pending(self, d) -> None:
        try:
            # Wait for DHCP to report the device's name (up to ~20 s).
            for _ in range(10):
                if d.hostname or d.approved:
                    break
                await asyncio.sleep(2)
            if d.approved:
                return
            await asyncio.sleep(2)  # a moment more so its first lookups show up
            kind = KIND.get(self._type(d), "device")
            lines = [f"🆕 <b>New {kind} wants internet</b>", "",
                     f"{self._icon(d)} <b>{e(self.engine.label(d))}</b>",
                     f"Maker: {e(self._maker(d))}",
                     f"IP: <code>{e(d.ip)}</code>"]
            twin = self._twin(d)
            if twin:
                lines += ["", f"💡 Looks like your <b>{e(self.engine.label(twin))}</b> again. It has the same "
                              "name, so it probably just reset its private Wi-Fi address."]
            doms = self._first_domains(d.mac)
            if doms:
                lines += ["", "First lookups: " + ", ".join(f"<code>{e(x)}</code>" for x in doms)]
            lines += ["", "It's on your Wi-Fi but has <b>no internet</b> until you decide."]
            mid = await self.send("\n".join(lines), [
                [btn("✅ Allow", f"ok:{d.mac}"), btn("✏️ Allow & name", f"okn:{d.mac}")],
                [btn("⚡ Allow at full speed", f"okf:{d.mac}"), btn("🚫 Block", f"bk:{d.mac}")],
            ])
            if mid and not d.approved:
                self._alert_msgs[d.mac] = mid
        finally:
            self._alerting.discard(d.mac)

    def device_joined(self, d) -> None:
        if not self._opt("alert_new", True):
            return
        self.notify(f"🆕 <b>New device joined</b>\n\n{self._icon(d)} <b>{e(self.engine.label(d))}</b>\n"
                    f"Maker: {e(self._maker(d))}\nIP: <code>{e(d.ip)}</code>",
                    [[btn("⚙️ Manage", f"dvn:{d.mac}"), btn("🚫 Block", f"bk:{d.mac}")]])

    def decision_made(self, d, verdict: str, via: str) -> None:
        """Turn the original alert into a result line with an undo button."""
        mid = self._alert_msgs.pop(d.mac, None)
        if not (mid and self.chat_id):
            return
        undo = btn("✅ Allow instead", f"ok:{d.mac}") if d.blocked else btn("🚫 Block instead", f"bk:{d.mac}")
        text = f"{verdict}: {self._icon(d)} <b>{e(self.engine.label(d))}</b> <i>(via {e(via)})</i>"
        asyncio.get_running_loop().create_task(self.edit(mid, text, [[undo, btn("⚙️ Manage", f"dvn:{d.mac}")]]))

    def device_resumed(self, d, expired: bool) -> None:
        if expired:
            self.notify(f"▶️ {self._icon(d)} <b>{e(self.engine.label(d))}</b> has internet again (pause ended).",
                        [[btn("⏸ Pause again 1 h", f"pz:60:{d.mac}"), btn("⚙️ Manage", f"dvn:{d.mac}")]])

    def outage_recovered(self, text: str) -> None:
        if self._opt("alert_outage", True):
            self.notify(text)

    # ================= screens =================
    def status_screen(self) -> tuple[str, list]:
        eng = self.engine
        now = time.time()
        devs = [d for d in eng.devices.values() if not d.is_self]
        online = [d for d in devs if now - d.last_seen < C.ONLINE_WINDOW]
        total_today = sum(sum(v) for v in eng.today.values())
        ab_on = bool(self.adblock and self.adblock.enabled)
        ads = sum(eng.ads_today.values())
        lines = [
            "📡 <b>Netwatch</b>", "",
            f"🟢 <b>{len(online)}</b> online · {len(devs) - len(online)} offline",
            f"⬇️ {fmt_rate(eng.totals[1])}   ⬆️ {fmt_rate(eng.totals[0])}",
            f"📦 Today: <b>{fmt_bytes(total_today)}</b>",
            f"🛡 Ad blocker: <b>{'ON' if ab_on else 'OFF'}</b>" + (f" · {ads:,} blocked today" if ab_on else ""),
        ]
        pend = sum(not d.approved for d in devs)
        blocked = sum(d.blocked for d in devs)
        paused = sum(d.paused_until > now for d in devs)
        flags = [x for x in (f"⏳ {pend} pending" if pend else "", f"🚫 {blocked} blocked" if blocked else "",
                             f"⏸ {paused} paused" if paused else "") if x]
        if flags:
            lines.append(" · ".join(flags))
        if eng.outage_start:
            lines.append(f"⚠️ Internet down since {hhmm(eng.outage_start)}")
        top = [d for d in sorted(devs, key=lambda d: -sum(eng.today.get(d.mac, [0, 0])))[:3]
               if sum(eng.today.get(d.mac, [0, 0]))]
        if top:
            lines += ["", "<b>Top today</b>"]
            lines += [f"{self._icon(d)} {e(eng.label(d))}: {fmt_bytes(sum(eng.today.get(d.mac, [0, 0])))}"
                      for d in top]
        lines += ["", f"<i>Updated {time.strftime('%H:%M:%S')}</i>"]
        buttons = [[btn("🔄 Refresh", "st"), btn("📱 Devices", "ls:0")],
                   [btn(f"🛡 Ad blocker: {'ON ✅' if ab_on else 'OFF ⬜'}", "tg:adblock:st")]]
        if pend:
            buttons.insert(0, [btn(f"⏳ Review {pend} pending", "pend")])
        return "\n".join(lines), buttons

    def devices_screen(self, page: int = 0) -> tuple[str, list]:
        eng = self.engine
        now = time.time()

        def rank(d):
            online = now - d.last_seen < C.ONLINE_WINDOW
            return (d.approved, not online, -sum(eng.today.get(d.mac, [0, 0])), eng.label(d).lower())
        devs = sorted((d for d in eng.devices.values()
                       if not d.is_self and (now - d.last_seen < 7 * 86400 or not d.approved)), key=rank)
        pages = max(1, (len(devs) + PAGE - 1) // PAGE)
        page = max(0, min(page, pages - 1))
        online = sum(now - d.last_seen < C.ONLINE_WINDOW for d in devs)
        rows = []
        for d in devs[page * PAGE:(page + 1) * PAGE]:
            st, _ = self._state(d)
            used = sum(eng.today.get(d.mac, [0, 0]))
            label = f"{st} {self._icon(d)} {eng.label(d)}"[:40]
            rows.append([btn(label + (f" · {fmt_bytes(used)}" if used >= 2**20 else ""), f"dv:{d.mac}")])
        nav = []
        if page > 0:
            nav.append(btn("‹ Prev", f"ls:{page - 1}"))
        nav.append(btn("🔄", f"ls:{page}"))
        if page < pages - 1:
            nav.append(btn("Next ›", f"ls:{page + 1}"))
        rows.append(nav)
        text = (f"📱 <b>Devices</b> · {online} online of {len(devs)}"
                + (f" · page {page + 1}/{pages}" if pages > 1 else "")
                + "\n\nTap a device to manage it.\n"
                  "🟢 online · ⚪ offline · ⚡ full speed · ⏸ paused · 🚫 blocked · ⏳ pending")
        return text, rows

    def device_screen(self, d) -> tuple[str, list]:
        eng = self.engine
        now = time.time()
        st, desc = self._state(d)
        today = eng.today.get(d.mac, [0, 0])
        lines = [f"{self._icon(d)} <b>{e(eng.label(d))}</b>", f"{st} {e(desc)}", ""]
        if d.hostname and d.hostname != eng.label(d):
            lines.append(f"Name on network: {e(d.hostname)}")
        lines += [f"Maker: {e(self._maker(d))}", f"IP <code>{e(d.ip)}</code> · MAC <code>{e(d.mac)}</code>"]
        if d.up_rate or d.down_rate:
            lines.append(f"Now: ⬇️ {fmt_rate(d.down_rate)}  ⬆️ {fmt_rate(d.up_rate)}")
        lines.append(f"Today: <b>{fmt_bytes(today[0] + today[1])}</b> "
                     f"(⬇️ {fmt_bytes(today[1])} ⬆️ {fmt_bytes(today[0])})")
        apps = [a for a in eng.store.services_for(d.mac, time.strftime("%Y-%m-%d"), 4) if a["bytes"] >= 2**20]
        if apps:
            lines.append("Top apps: " + ", ".join(f"{e(a['service'])} {fmt_bytes(a['bytes'])}" for a in apps))
        ads = eng.ads_today.get(d.mac, 0)
        if ads:
            lines.append(f"🛡 {ads:,} ads/trackers blocked today")
        if d.bypass:
            lines += ["", "<i>Full speed: traffic skips Netwatch, so it can't be blocked or paused, "
                          "and its data isn't counted.</i>"]
        lines += ["", f"<i>Updated {time.strftime('%H:%M:%S')}</i>"]

        m = d.mac
        rows: list[list[dict]] = []
        if not d.is_self:
            if not d.approved:
                rows += [[btn("✅ Allow", f"ok:{m}"), btn("⚡ Allow at full speed", f"okf:{m}")],
                         [btn("🚫 Block", f"bk:{m}")]]
            elif d.blocked:
                rows.append([btn("✅ Unblock", f"ub:{m}")])
            elif d.paused_until > now:
                rows.append([btn(f"▶️ Resume now (paused until {hhmm(d.paused_until)})", f"rs:{m}")])
            elif d.bypass:
                rows.append([btn("⚡ Full speed is ON: turn off", f"bp0:{m}")])
            else:
                rows.append([btn("⏸ 30 min", f"pz:30:{m}"), btn("⏸ 1 hour", f"pz:60:{m}"),
                             btn("⏸ Till 7:00", f"pz:am:{m}")])
                rows.append([btn("🚫 Block", f"bk:{m}"), btn("⚡ Full speed", f"bp1:{m}")])
            rows.append([btn("✏️ Rename", f"rn:{m}"), btn("🔄 Refresh", f"dv:{m}"), btn("« Devices", "ls:0")])
        else:
            rows.append([btn("🔄 Refresh", f"dv:{m}"), btn("« Devices", "ls:0")])
        return "\n".join(lines), rows

    def pending_screen(self) -> tuple[str, list]:
        pend = [d for d in self.engine.devices.values() if not d.approved]
        if not pend:
            return "⏳ <b>Pending</b>\n\nNo devices are waiting for approval. 👍", [[btn("📱 Devices", "ls:0")]]
        rows = []
        for d in pend[:10]:
            rows.append([btn(f"{self._icon(d)} {self.engine.label(d)}"[:32], f"dv:{d.mac}")])
            rows.append([btn("✅ Allow", f"ok:{d.mac}"), btn("🚫 Block", f"bk:{d.mac}")])
        return f"⏳ <b>{len(pend)} waiting for approval</b>\n\nThey have no internet until you decide.", rows

    def blocked_screen(self) -> tuple[str, list]:
        now = time.time()
        devs = [d for d in self.engine.devices.values() if d.blocked or d.paused_until > now]
        if not devs:
            return ("🚫 <b>Blocked &amp; paused</b>\n\nNothing is blocked or paused right now.",
                    [[btn("📱 Devices", "ls:0")]])
        rows = []
        for d in devs[:15]:
            lab = f"{self._icon(d)} {self.engine.label(d)}"[:26]
            if d.blocked:
                rows.append([btn(f"✅ Unblock {lab}", f"ub:{d.mac}:b")])
            else:
                rows.append([btn(f"▶️ Resume {lab} ({hhmm(d.paused_until)})", f"rs:{d.mac}:b")])
        return "🚫 <b>Blocked &amp; paused</b>\n\nTap to give internet back.", rows

    def settings_screen(self) -> tuple[str, list]:
        s = load_settings()
        ab = self.adblock

        def tg(on) -> str:
            return "✅ ON" if on else "⬜ OFF"
        rows = [
            [btn(f"🛡 Ad blocker: {tg(ab and ab.enabled)}", "tg:adblock")],
            [btn(f"🔒 Force Netwatch DNS: {tg(s.get('dns_force'))}", "tg:dnsforce")],
            [btn(f"🆕 Approve new devices: {tg(s.get('require_approval'))}", "tg:approval")],
            [btn(f"📶 Outage alerts: {tg(s.get('alert_outage', True))}", "tg:outage")],
            [btn(f"📊 Weekly report (Sun 20:00): {tg(s.get('weekly_report', True))}", "tg:weekly")],
        ]
        text = ("⚙️ <b>Settings</b>\n\n"
                "🛡 <b>Ad blocker</b>: blocks ads and trackers for every device.\n"
                "🔒 <b>Force DNS</b>: devices that ignore Netwatch (hardcoded or encrypted DNS) have to use it.\n"
                "🆕 <b>Approve new devices</b>: unknown devices get no internet until you allow them.")
        if ab and ab.enabled:
            text += f"\n\nBlocking {ab.domains:,} domains · {sum(self.engine.ads_today.values()):,} blocked today."
        return text, rows

    async def send_report(self, key: str = "7d") -> None:
        from .charts import report_png
        from .insights import compute, report_text
        ins = await asyncio.to_thread(compute, self.engine, key)
        text = await asyncio.to_thread(report_text, self.engine, key)
        if self.adblock and self.adblock.enabled:
            text += f"\n\n🛡 Ads/trackers blocked today: {sum(self.engine.ads_today.values()):,}"
        buttons = [[btn("Today", "rp:today"), btn("7 days", "rp:7d"), btn("30 days", "rp:30d")]]
        try:
            png = await asyncio.to_thread(report_png, ins, key)
        except Exception:
            log.exception("chart render failed")
            png = None
        if png and len(text) <= 1024:
            await self.send_photo(png, text, buttons)
        elif png:
            await self.send_photo(png, text.split("\n\n")[0])
            await self.send(text, buttons)
        else:
            await self.send(text, buttons)

    # ================= polling =================
    async def run(self) -> None:
        if not self.configured:
            log.info("telegram disabled (no bot token; add one in Settings)")
            return
        backoff = 5
        while True:
            try:
                if not self.username:
                    me = await self.call("getMe")
                    self.username = me.get("username", "")
                    await self.call("setMyCommands", commands=[
                        {"command": "menu", "description": "Show the button menu"},
                        {"command": "status", "description": "Network status"},
                        {"command": "devices", "description": "Manage devices"},
                        {"command": "report", "description": "Usage report with chart"},
                    ])
                    log.info("telegram bot @%s ready", self.username)
                updates = await self.call("getUpdates", http_timeout=70, offset=self._offset, timeout=50,
                                          allowed_updates=["message", "callback_query"])
                for u in updates:
                    self._offset = u["update_id"] + 1
                    try:
                        await self._handle(u)
                    except Exception:
                        log.exception("telegram update failed")
                backoff = 5
            except asyncio.CancelledError:
                raise
            except Exception as err:
                lvl = logging.INFO if "timed out" in str(err) else logging.WARNING
                log.log(lvl, "telegram poll error: %s (retry in %ss)", err, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    async def weekly_report_loop(self) -> None:
        """Send the 7-day report every Sunday at 20:00 local time."""
        while True:
            await asyncio.sleep(300)
            t = time.localtime()
            week = time.strftime("%G-W%V", t)
            s = load_settings()
            if t.tm_wday == 6 and t.tm_hour == 20 and s.get("last_weekly") != week \
                    and s.get("weekly_report", True) and self.configured and self.chat_id:
                try:
                    await self.send_report("7d")
                    save_settings(last_weekly=week)
                except Exception:
                    log.exception("weekly report failed")

    async def _handle(self, u: dict) -> None:
        if "callback_query" in u:
            await self._on_button(u["callback_query"])
            return
        msg = u.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat or not text:
            return
        if chat != self.chat_id:
            await self._handle_stranger(chat, msg, text)
            return

        # A pending rename takes the next plain message.
        if self._rename and not text.startswith("/") and text not in MENU_BUTTONS:
            mac, card, deadline = self._rename
            self._rename = None
            if time.time() < deadline and mac in self.engine.devices:
                d = self.engine.update_device(mac, text[:40], None)
                await self.send(f"✏️ Renamed to <b>{e(self.engine.label(d))}</b>", markup=MENU)
                await self.show(card, *self.device_screen(d))
                return

        words = text.split()
        cmd = words[0].split("@")[0].lower()
        if cmd in ("/start", "/menu", "/help"):
            await self.send("👋 <b>Netwatch</b>: your home network in your pocket.\n\n"
                            "Use the buttons below. I'll also message you when a new device joins, "
                            "when the internet drops, and every Sunday with a report.", markup=MENU)
        elif cmd == "/status" or text == B_STATUS:
            await self.send(*self.status_screen())
        elif cmd == "/devices" or text == B_DEVICES:
            await self.send(*self.devices_screen())
        elif cmd == "/pending" or text == B_PENDING:
            await self.send(*self.pending_screen())
        elif cmd == "/blocked" or text == B_BLOCKED:
            await self.send(*self.blocked_screen())
        elif cmd == "/report" or text == B_REPORT:
            arg = words[1].lower() if cmd == "/report" and len(words) > 1 else "7d"
            await self.send_report(arg if arg in ("today", "7d", "30d") else "7d")
        elif cmd == "/settings" or text == B_SETTINGS:
            await self.send(*self.settings_screen())
        else:
            await self.send("Use the buttons below 👇", markup=MENU)

    async def _handle_stranger(self, chat: int, msg: dict, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        if cmd.split("@")[0].lower() == "/pair" and self._pair_code and time.time() < self._pair_code[1] \
                and secrets.compare_digest(arg.strip(), self._pair_code[0]):
            self._pair_code = None
            who = msg.get("from") or {}
            name = " ".join(filter(None, [who.get("first_name"), who.get("last_name")])) or who.get("username", "")
            save_settings(telegram_chat=chat, telegram_chat_name=name, require_approval=True)
            self.engine.settings_changed()
            await self.send("✅ <b>Paired with Netwatch.</b>\n\nNew devices now need your approval before they "
                            "get internet. Use the buttons below to manage your network.", markup=MENU)
            log.info("telegram paired with chat %s", chat)
        else:
            await self.call("sendMessage", chat_id=chat,
                            text="This is a private bot. To pair, open Netwatch → Settings → Telegram.")

    async def _on_button(self, q: dict) -> None:
        message = q.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        mid = message.get("message_id")

        async def answer(text: str = "", alert: bool = False):
            try:
                await self.call("answerCallbackQuery", callback_query_id=q["id"], text=text[:190],
                                show_alert=alert)
            except Exception:
                pass

        if chat != self.chat_id:
            return await answer("Not allowed")
        parts = (q.get("data") or "").split(":")
        action, eng = parts[0], self.engine

        # ---- screens & toggles ----
        if action == "st":
            await answer()
            return await self.show(mid, *self.status_screen())
        if action == "ls":
            await answer()
            return await self.show(mid, *self.devices_screen(int(parts[1]) if len(parts) > 1 else 0))
        if action == "pend":
            await answer()
            return await self.show(mid, *self.pending_screen())
        if action == "rp":
            await answer("Building the report…")
            return await self.send_report(parts[1] if len(parts) > 1 else "7d")
        if action == "tg":
            return await self._toggle(parts[1], mid, answer, back_to_status=len(parts) > 2)

        # ---- device actions: <action>[:<arg>]:<mac (6 parts)>[:b] ----
        arg = parts[1] if action == "pz" else None
        mac_parts = parts[2:8] if action == "pz" else parts[1:7]
        mac = ":".join(mac_parts)
        from_blocked_list = parts[-1] == "b"
        d = eng.devices.get(mac)
        if d is None:
            return await answer("That device is gone", alert=True)
        try:
            if action == "dv":
                await answer()
                return await self.show(mid, *self.device_screen(d))
            if action == "dvn":
                await answer()
                return await self.send(*self.device_screen(d))
            if action in ("ok", "okn", "okf", "bk"):
                verdict = {"ok": "allow", "okn": "allow", "okf": "allow_bypass", "bk": "block"}[action]
                on_card = (message.get("text") or "").startswith(self._icon(d))
                if not on_card and mid:
                    self._alert_msgs[mac] = mid  # the result line replaces this message
                eng.decide(mac, verdict, via="Telegram")
                await answer({"allow": "Allowed ✅", "allow_bypass": "Allowed at full speed ⚡",
                              "block": "Blocked 🚫"}[verdict])
                if action == "okn":
                    await self._ask_rename(d, None)
                if not on_card:
                    return
            elif action == "ub":
                eng.update_device(mac, None, False)
                await answer("Unblocked ✅")
            elif action == "rs":
                eng.resume(mac, via="Telegram")
                await answer("Internet is back on ▶️")
            elif action == "pz":
                until = next_morning() if arg == "am" else time.time() + int(arg) * 60
                eng.pause(mac, until, via="Telegram")
                await answer(f"Paused until {hhmm(until)} ⏸")
                if not mid or not (message.get("text") or "").startswith(self._icon(d)):
                    return await self.send(*self.device_screen(d))
            elif action in ("bp1", "bp0"):
                eng.update_device(mac, None, None, bypass=(action == "bp1"))
                await answer("Full speed is on. It applies when the device reconnects (toggle its Wi-Fi). ⚡"
                             if action == "bp1" else "Routed through Netwatch again once it reconnects.",
                             alert=True)
            elif action == "rn":
                await answer()
                return await self._ask_rename(d, mid)
            else:
                return await answer()
        except ValueError as err:
            return await answer(str(err), alert=True)
        await self.show(mid, *(self.blocked_screen() if from_blocked_list else self.device_screen(d)))

    async def _ask_rename(self, d, card_mid: int | None) -> None:
        self._rename = (d.mac, card_mid, time.time() + 300)
        await self.send(f"✏️ Send a new name for <b>{e(self.engine.label(d))}</b>",
                        markup={"force_reply": True, "input_field_placeholder": "e.g. Ahmed's Watch"})

    async def _toggle(self, what: str, mid: int | None, answer, back_to_status: bool = False) -> None:
        s = load_settings()
        if what == "adblock" and self.adblock:
            on = not self.adblock.enabled
            await answer("Ad blocker " + ("turning on…" if on else "off"))
            await self.adblock.set_enabled(on, via="Telegram")
        elif what == "dnsforce" and self.adblock:
            on = not self.adblock.force_dns
            await self.adblock.set_force_dns(on, via="Telegram")
            await answer("Force DNS " + ("on" if on else "off"))
        elif what == "approval":
            save_settings(require_approval=not s.get("require_approval", False))
            self.engine.settings_changed()
            await answer("Saved")
        elif what == "outage":
            save_settings(alert_outage=not s.get("alert_outage", True))
            await answer("Saved")
        elif what == "weekly":
            save_settings(weekly_report=not s.get("weekly_report", True))
            await answer("Saved")
        else:
            await answer()
        self.engine._build_snapshot()
        await self.show(mid, *(self.status_screen() if back_to_status else self.settings_screen()))
