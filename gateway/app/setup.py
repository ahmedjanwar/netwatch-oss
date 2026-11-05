"""Live checks behind the setup guide, plus the persisted gateway-mode setting."""
import asyncio
import json
import logging
import os
import secrets
import select
import socket
import struct
import time
from pathlib import Path

from . import config as C
from .discovery import run_cmd

log = logging.getLogger("netwatch.setup")
_SETTINGS = Path(C.DATA) / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**changes) -> None:
    data = load_settings() | changes
    tmp = _SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, _SETTINGS)


def apply_saved_mode() -> None:
    """data/settings.json (written by the UI or gateway-mode.sh) beats .env."""
    saved = load_settings().get("dhcp")
    if saved is not None:
        C.DHCP_ENABLED = bool(saved)


# ---------- checks ----------
async def static_ip() -> bool | None:
    out = await run_cmd("ip", "-j", "-4", "addr", "show", "dev", C.IFACE)
    try:
        for iface in json.loads(out or "[]"):
            for a in iface.get("addr_info", []):
                if a.get("local") == C.SELF_IP:
                    return not a.get("dynamic", False)
    except ValueError:
        return None
    return False


async def ipv6_router_advertising() -> bool:
    out = await run_cmd("ip", "-6", "route", "show", "default", "dev", C.IFACE)
    return "proto ra" in out


def probe_dhcp(timeout: float = 4.0) -> list[str] | None:
    """Broadcast a DHCPDISCOVER and list the server IDs that answer.

    Uses a random, locally administered MAC and never sends a REQUEST, so no
    lease is actually taken. Returns None if we can't probe (port 68 busy).
    """
    xid = secrets.randbits(32)
    mac = bytes([0x02]) + secrets.token_bytes(5)
    pkt = struct.pack("!BBBBIHH", 1, 1, 6, 0, xid, 0, 0x8000)  # broadcast flag
    pkt += b"\x00" * 16 + mac + b"\x00" * 10 + b"\x00" * 192
    pkt += bytes([99, 130, 83, 99, 53, 1, 1, 55, 3, 1, 3, 6, 255])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, C.IFACE.encode())
        s.bind(("0.0.0.0", 68))
    except OSError as e:
        log.info("dhcp probe unavailable: %s", e)
        s.close()
        return None
    servers: set[str] = set()
    try:
        s.sendto(pkt, ("255.255.255.255", 67))
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            ready, _, _ = select.select([s], [], [], left)
            if not ready:
                break
            data, (src, _port) = s.recvfrom(2048)
            if len(data) < 240 or data[0] != 2 or struct.unpack("!I", data[4:8])[0] != xid:
                continue
            server = src
            i = 240
            while i + 1 < len(data) and data[i] != 255:
                if data[i] == 0:
                    i += 1
                    continue
                code, ln = data[i], data[i + 1]
                if code == 54 and ln == 4:
                    server = socket.inet_ntoa(data[i + 2:i + 6])
                i += 2 + ln
            servers.add(server)
    finally:
        s.close()
    return sorted(servers)


class SetupChecks:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._dhcp: tuple[float, list[str] | None] = (0, None)

    async def dhcp_servers(self, force: bool) -> list[str] | None:
        async with self._lock:
            ts, val = self._dhcp
            if force or time.time() - ts > 60:
                val = await asyncio.to_thread(probe_dhcp)
                self._dhcp = (time.time(), val)
            return val

    async def status(self, engine, force: bool = False) -> dict:
        static, ra, servers = await asyncio.gather(
            static_ip(), ipv6_router_advertising(), self.dhcp_servers(force))
        now = time.time()
        devices = [d for d in engine.devices.values() if not d.is_self]
        online = [d for d in devices if now - d.last_seen < C.ONLINE_WINDOW]
        routed = [d for d in online if now - d.last_routed < 120]
        others = None if servers is None else [s for s in servers if s != C.SELF_IP]
        return {
            "self_ip": C.SELF_IP, "router_ip": C.ROUTER_IP, "iface": C.IFACE, "router_name": engine.router_name(),
            "static_ip": static,
            "router_dhcp_on": None if others is None else bool(others),
            "dhcp_servers": servers,
            "ipv6_ra": ra,
            "gateway_mode": C.DHCP_ENABLED,
            "online": len(online),
            "routed": len(routed),
            "not_routed": sorted(engine.label(d) for d in online if d not in routed),
            "checked_at": self._dhcp[0],
        }
