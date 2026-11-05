"""Parse `conntrack -L -o id` output into flows."""
import ipaddress
from dataclasses import dataclass

_DOCKER = ipaddress.ip_network("172.16.0.0/12")


@dataclass(slots=True)
class Flow:
    fid: str
    proto: str
    src: str
    dst: str
    sport: int
    dport: int
    up: int    # bytes sent by the local side (original direction)
    down: int  # bytes received (reply direction)


def parse_line(line: str) -> Flow | None:
    parts = line.split()
    if len(parts) < 4:
        return None
    proto = parts[0]
    orig: dict[str, str] = {}
    reply: dict[str, str] = {}
    cur = orig
    srcs = 0
    fid = ""
    for tok in parts[1:]:
        k, sep, v = tok.partition("=")
        if not sep:
            continue
        if k == "src":
            srcs += 1
            cur = orig if srcs == 1 else reply
        if k == "id":
            fid = v
        elif k not in cur:
            cur[k] = v
    if "src" not in orig or "dst" not in orig:
        return None
    try:
        return Flow(
            fid=fid or f"{proto}:{orig['src']}:{orig.get('sport')}:{orig['dst']}:{orig.get('dport')}",
            proto=proto,
            src=orig["src"],
            dst=orig["dst"],
            sport=int(orig.get("sport", 0)),
            dport=int(orig.get("dport", 0)),
            up=int(orig.get("bytes", 0)),
            down=int(reply.get("bytes", 0)),
        )
    except ValueError:
        return None


def owner_of(flow: Flow, lan: ipaddress.IPv4Network, self_ip: str) -> str | None:
    """Return the local IP responsible for an internet-bound flow, else None.

    Flows from the gateway itself (including its Docker containers) are
    attributed to self_ip.
    """
    try:
        src = ipaddress.ip_address(flow.src)
        dst = ipaddress.ip_address(flow.dst)
    except ValueError:
        return None
    if not dst.is_global or dst.is_multicast:
        return None
    if flow.src == self_ip or src in _DOCKER:
        return self_ip
    if src in lan:
        return flow.src
    return None
