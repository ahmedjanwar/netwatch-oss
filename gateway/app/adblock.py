"""Network-wide ad blocking via a dnsmasq hosts file, with an on/off switch."""
import asyncio
import logging
import re
import time
import urllib.request
from pathlib import Path

from . import config as C
from .setup import load_settings, save_settings

log = logging.getLogger("netwatch.adblock")

LIST_URL = "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
SOURCE = Path(C.DATA) / "blocklist-source.hosts"
ACTIVE = Path(C.DATA) / "adblock.hosts"
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_NEVER = {"localhost", "localhost.localdomain", "local", "broadcasthost", "0.0.0.0", "ip6-localhost",
          "ip6-loopback"}
REFRESH_EVERY = 86400


def _settings() -> dict:
    s = load_settings()
    return {
        "enabled": bool(s.get("adblock", False)),
        "force_dns": bool(s.get("dns_force", False)),
        "allowlist": list(s.get("adblock_allow", [])),
    }


def clean_allowlist(text_or_list) -> list[str]:
    items = text_or_list if isinstance(text_or_list, list) else re.split(r"[\s,]+", text_or_list or "")
    out = []
    for raw in items:
        d = raw.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0].strip(".")
        if d.startswith("*."):
            d = d[2:]
        if d and _DOMAIN.match(d) and d not in out:
            out.append(d)
    return out[:500]


class AdBlocker:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.domains = 0
        self.source_domains = 0
        self.updated = SOURCE.stat().st_mtime if SOURCE.exists() else 0
        self.last_error = ""
        self._lock = asyncio.Lock()

    # ---------- state ----------
    @property
    def enabled(self) -> bool:
        return _settings()["enabled"]

    @property
    def force_dns(self) -> bool:
        return _settings()["force_dns"]

    def status(self) -> dict:
        s = _settings()
        return {
            "enabled": s["enabled"], "force_dns": s["force_dns"], "allowlist": s["allowlist"],
            "domains": self.domains if s["enabled"] else 0, "list_domains": self.source_domains,
            "updated": self.updated, "error": self.last_error,
            "blocked_today": sum(self.engine.ads_today.values()),
        }

    # ---------- list handling ----------
    def _download(self) -> int:
        req = urllib.request.Request(LIST_URL, headers={"User-Agent": "netwatch/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(30 * 2**20)
        text = data.decode("utf-8", errors="replace")
        count = sum(1 for _ in self._parse(text))
        if count < 10000:
            raise RuntimeError(f"blocklist looks wrong ({count} domains)")
        tmp = SOURCE.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(SOURCE)
        return count

    @staticmethod
    def _parse(text: str):
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2 or parts[0] not in ("0.0.0.0", "127.0.0.1"):
                continue
            for d in parts[1:]:
                d = d.lower().strip(".")
                if d not in _NEVER and _DOMAIN.match(d):
                    yield d

    def _build(self) -> int:
        """Write the active hosts file (empty when disabled). Returns the domain count."""
        s = _settings()
        lines: list[str] = []
        n = 0
        if s["enabled"] and SOURCE.exists():
            allow = s["allowlist"]
            domains = set(self._parse(SOURCE.read_text(errors="replace")))
            self.source_domains = len(domains)
            for d in sorted(domains):
                if any(d == a or d.endswith("." + a) for a in allow):
                    continue
                # Both families, so AAAA lookups are answered too instead of forwarded.
                lines.append(f"0.0.0.0 {d}\n:: {d}\n")
                n += 1
        tmp = ACTIVE.with_suffix(".tmp")
        tmp.write_text("".join(lines))
        tmp.replace(ACTIVE)
        return n

    async def apply(self) -> None:
        async with self._lock:
            self.domains = await asyncio.to_thread(self._build)
        self.engine.dns.reload()
        log.info("ad blocker %s (%d domains)", "ON" if self.enabled else "OFF", self.domains)

    async def refresh(self, force: bool = False) -> None:
        if not force and SOURCE.exists() and time.time() - SOURCE.stat().st_mtime < REFRESH_EVERY:
            return
        try:
            self.source_domains = await asyncio.to_thread(self._download)
            self.updated = time.time()
            self.last_error = ""
            log.info("blocklist updated: %d domains", self.source_domains)
        except Exception as e:
            self.last_error = f"Couldn't update the blocklist: {e}"
            log.warning("%s", self.last_error)
            return
        await self.apply()

    # ---------- user actions ----------
    async def set_enabled(self, on: bool, via: str = "dashboard") -> None:
        save_settings(adblock=on)
        if on and not SOURCE.exists():
            await self.refresh(force=True)
        await self.apply()
        self.engine.event("adblock", f"Ad blocker turned {'ON' if on else 'OFF'} (via {via})")

    async def set_force_dns(self, on: bool, via: str = "dashboard") -> None:
        from . import netfilter
        save_settings(dns_force=on)
        netfilter.set_dns_force(on)
        self.engine.dns.restart()  # applies/removes the Firefox DoH canary
        self.engine.event("adblock", f"Force Netwatch DNS turned {'ON' if on else 'OFF'} (via {via})")

    async def set_allowlist(self, items) -> list[str]:
        allow = clean_allowlist(items)
        save_settings(adblock_allow=allow)
        await self.apply()
        return allow

    async def run(self) -> None:
        """Startup + daily refresh."""
        await self.apply()
        while True:
            if self.enabled:
                await self.refresh()
            await asyncio.sleep(3600)
