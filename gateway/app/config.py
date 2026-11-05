"""Runtime configuration.

Each value comes from, in order: what the setup wizard saved in data/settings.json,
an NW_* environment variable, or what Netwatch detects on this machine. There are no
built-in network defaults: a fresh install looks at its own network and uses that.
"""
import ipaddress
import json
import logging
import os
import subprocess

log = logging.getLogger("netwatch.config")

DATA = os.environ.get("NW_DATA", "").strip() or "/data"


def _saved() -> dict:
    try:
        with open(os.path.join(DATA, "settings.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _ip_json(*args: str) -> list:
    try:
        out = subprocess.run(["ip", "-j", *args], capture_output=True, text=True, timeout=5).stdout
        return json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def detect() -> dict:
    """The interface with the default route, this machine's address on it, its LAN and the router."""
    found: dict = {}
    for r in _ip_json("-4", "route", "show", "default"):
        if r.get("dev") and r.get("gateway"):
            found = {"iface": r["dev"], "router_ip": r["gateway"]}
            break
    if not found:
        return found
    for iface in _ip_json("-4", "addr", "show", "dev", found["iface"]):
        for a in iface.get("addr_info", []):
            if a.get("family") == "inet" and a.get("local"):
                found["self_ip"] = a["local"]
                found["lan"] = str(ipaddress.ip_network(f"{a['local']}/{a['prefixlen']}", strict=False))
                found["dynamic"] = bool(a.get("dynamic", False))
                return found
    return found


def interfaces() -> list[dict]:
    """IPv4 interfaces the user could pick in setup (not loopback, Docker or VPN tunnels)."""
    skip = ("lo", "docker", "br-", "veth", "tailscale", "wg", "tun", "virbr")
    out = []
    for iface in _ip_json("-4", "addr", "show"):
        name = iface.get("ifname", "")
        if name.startswith(skip):
            continue
        for a in iface.get("addr_info", []):
            if a.get("family") == "inet":
                out.append({"name": name, "ip": a["local"],
                            "lan": str(ipaddress.ip_network(f"{a['local']}/{a['prefixlen']}", strict=False))})
    return out


def default_dhcp_ranges(lan: ipaddress.IPv4Network, self_ip: str, router_ip: str) -> list[list[str]]:
    """Hand out the LAN's addresses, skipping this machine and the router."""
    hosts = list(lan.hosts())[:254] if lan.num_addresses <= 65536 else []
    taken = set()
    for x in (self_ip, router_ip):
        try:
            taken.add(ipaddress.ip_address(x))
        except ValueError:
            pass
    runs, cur = [], []
    for h in hosts:
        if h in taken:
            if len(cur) >= 5:
                runs.append(cur)
            cur = []
        else:
            cur.append(h)
    if len(cur) >= 5:
        runs.append(cur)
    return [[str(r[0]), str(r[-1])] for r in runs]


_s = _saved()
_net = _s.get("network", {})
_detected = detect()


def _pick(key: str, env: str, default: str = "") -> str:
    return str(_net.get(key) or os.environ.get(env, "").strip() or _detected.get(key) or default)


IFACE = _pick("iface", "NW_IFACE", "eth0")
SELF_IP = _pick("self_ip", "NW_SELF_IP")
ROUTER_IP = _pick("router_ip", "NW_ROUTER_IP")
LAN = ipaddress.ip_network(_pick("lan", "NW_LAN", f"{SELF_IP or '192.168.1.2'}/24"), strict=False)
ROUTER_NAME = str(_net.get("router_name") or os.environ.get("NW_ROUTER_NAME", "").strip())
if not SELF_IP or not ROUTER_IP:
    log.warning("couldn't detect this machine's network (no default route?); finish setup at /welcome")

# Gateway mode: when on, this box serves DHCP and hands itself out as the
# default gateway + DNS server, so all traffic flows through it.
DHCP_ENABLED = bool(_s.get("dhcp", os.environ.get("NW_DHCP", "0") == "1"))
_ranges = os.environ.get("NW_DHCP_RANGES", "").strip()
DHCP_RANGES = ([r.split(",") for r in _ranges.split(";")] if _ranges
               else default_dhcp_ranges(LAN, SELF_IP, ROUTER_IP))
DHCP_LEASE = os.environ.get("NW_DHCP_LEASE", "").strip() or "1h"

DNS_UPSTREAMS = (os.environ.get("NW_DNS_UPSTREAMS", "").strip() or "9.9.9.9,149.112.112.112").split(",")

# The speed of this machine's network link in Mb/s, if the user told us. Insights only
# warns about a saturated link when it knows the limit.
_link = _s.get("link_mbps") or os.environ.get("NW_LINK_MBPS", "").strip()
LINK_MBPS = float(_link) if _link else None

TELEGRAM_TOKEN = str(_s.get("telegram_token") or os.environ.get("NW_TELEGRAM_TOKEN", "")).strip()

PORT = int(os.environ.get("NW_PORT", "").strip() or "8088")
PASSWORD = os.environ.get("NW_PASSWORD", "")
POLL_SECONDS = float(os.environ.get("NW_POLL", "").strip() or "2")
ONLINE_WINDOW = 180  # seconds since last sighting to count as online
ARP_SCAN_EVERY = 60
