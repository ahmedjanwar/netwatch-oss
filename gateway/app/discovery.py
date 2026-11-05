"""Find devices on the LAN: ARP scan, kernel neighbour table, PTR names, vendors."""
import asyncio
import json
import logging
import re
from pathlib import Path

import dns.resolver
import dns.reversename

from . import config as C

log = logging.getLogger("netwatch.discovery")
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_OUI: dict[str, str] = {}


async def _run(*cmd: str, timeout: float = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ""
    return out.decode(errors="replace")


async def run_cmd(*cmd: str, timeout: float = 30) -> str:
    return await _run(*cmd, timeout=timeout)


def valid_mac(mac: str) -> bool:
    return bool(_MAC.match(mac)) and mac not in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff")


async def arp_scan() -> list[tuple[str, str]]:
    out = await _run("arp-scan", "-I", C.IFACE, "-l", "-q", "-x", "-g", "-r", "2", timeout=60)
    found = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and valid_mac(parts[1].lower()):
            found.append((parts[0], parts[1].lower()))
    return found


async def neighbours() -> list[tuple[str, str, str]]:
    """(ip, mac, state) from the kernel ARP table."""
    out = await _run("ip", "-4", "-j", "neigh", "show", "dev", C.IFACE)
    try:
        entries = json.loads(out or "[]")
    except json.JSONDecodeError:
        return []
    res = []
    for e in entries:
        mac = (e.get("lladdr") or "").lower()
        states = e.get("state") or []
        if valid_mac(mac) and "FAILED" not in states and "INCOMPLETE" not in states:
            res.append((e["dst"], mac, states[0] if states else ""))
    return res


def ptr_lookup(ip: str) -> str | None:
    """Ask the router (and ourselves) for the device's hostname."""
    for server in (C.ROUTER_IP, C.SELF_IP):
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = [server]
        r.lifetime = 2
        try:
            ans = r.resolve(dns.reversename.from_address(ip), "PTR")
            name = str(ans[0]).rstrip(".")
            if name and not name.endswith(".arpa"):
                return name.split(".")[0]
        except Exception:
            continue
    return None


def load_oui() -> None:
    # Wireshark's list is the most complete; arp-scan's bundled list is the fallback.
    manuf = Path("/usr/share/netwatch/manuf")
    if manuf.exists():
        for line in manuf.read_text(errors="replace").splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            prefix = parts[0].strip().replace(":", "").replace("-", "").upper()
            if len(prefix) == 6 and len(parts) >= 2:
                _OUI[prefix] = (parts[2] if len(parts) > 2 and parts[2] else parts[1]).strip()
    for path in ("/usr/share/arp-scan/ieee-oui.txt", "/usr/share/arp-scan/mac-vendor.txt"):
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            if not line or line.startswith("#"):
                continue
            prefix, _, vendor = line.partition("\t")
            if len(prefix) == 6:
                _OUI.setdefault(prefix.upper(), vendor.strip())
    log.info("loaded %d vendor prefixes", len(_OUI))


def vendor_of(mac: str) -> str:
    first = int(mac[:2], 16)
    if first & 0x02:
        return "Private MAC"
    return _OUI.get(mac.replace(":", "")[:6].upper(), "")


# Words in registered vendor names that aren't part of the brand people know.
_VENDOR_NOISE = re.compile(r"\b(inc|incorporated|corp|corporation|co|ltd|limited|llc|gmbh|ag|sa|sas|"
                           r"bv|plc|technologies|technology|tech|electronics|communications|"
                           r"networks?|international|holdings?|group|usa|ch)\b\.?", re.I)


def short_vendor(vendor: str) -> str:
    """The brand people know: "TP-LINK TECHNOLOGIES CO.,LTD." -> "TP-Link", "NETGEAR" -> "Netgear"."""
    if not vendor or vendor == "Private MAC":
        return ""
    words = _VENDOR_NOISE.sub(" ", vendor.replace(",", " ")).split()
    name = words[0].strip(" .-") if words else ""
    if not name:
        return ""
    if len(name.replace("-", "")) <= 4:
        return name.upper()  # AVM, ZTE, TCL
    if name.isupper() or name.islower():
        return name.title().replace("Tp-Link", "TP-Link")
    return name


def mac_of(ip: str) -> str:
    """The MAC the kernel has cached for a LAN address, or "" if it hasn't talked to it."""
    try:
        for line in open("/proc/net/arp").read().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4 and parts[0] == ip and valid_mac(parts[3]):
                return parts[3].lower()
    except OSError:
        pass
    return ""


_TYPES = [
    ("console", ("playstation", "sony interactive", "nintendo", "xbox", "steamdeck")),
    ("tv", ("tv", "roku", "chromecast", "firetv", "fire-tv", "bravia", "webos", "tizen",
            "hisense", "tcl", "vizio", "apple-tv", "appletv", "shield")),
    ("speaker", ("sonos", "echo", "homepod", "bose", "google-home", "nest-audio")),
    ("iot", ("espressif", "tuya", "shelly", "sonoff", "nest", "ecobee", "philips",
             "signify", "hue", "wiz", "smart", "camera", "cam", "printer", "epson", "brother",
             "canon", "vacuum", "roborock", "dreame", "ezviz", "hikvision")),
    ("watch", ("watch", "galaxy-watch", "fitbit", "garmin", "amazfit")),
    ("tablet", ("ipad", "tablet", "kindle")),
    ("phone", ("iphone", "android", "galaxy", "pixel", "oneplus", "xiaomi", "redmi",
               "huawei", "oppo", "vivo", "motorola", "nothing", "phone")),
    ("computer", ("macbook", "imac", "mac-mini", "laptop", "desktop", "-pc", "pc-", "windows",
                  "dell", "lenovo", "hewlett", "asus", "toshiba", "acer", "msi", "intel",
                  "raspberry", "ubuntu", "linux", "thinkpad", "surface")),
]


def guess_type(vendor: str, hostname: str, name: str) -> str:
    text = f"{name} {hostname} {vendor}".lower()
    for kind, needles in _TYPES:
        if any(n in text for n in needles):
            return kind
    if vendor == "Private MAC":
        return "phone"  # phones/laptops randomize; phones are the common case
    if "apple" in text:
        return "phone"
    return "unknown"
