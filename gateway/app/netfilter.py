"""Kernel networking: sysctls and the nftables table that does NAT + blocking."""
import logging
import subprocess

from . import config as C

log = logging.getLogger("netwatch.netfilter")
TABLE = "netwatch"


def _sysctl(path: str, value: str) -> None:
    try:
        with open(path, "w") as f:
            f.write(value)
    except OSError as e:
        log.warning("could not set %s=%s: %s", path, value, e)


def _nft(script: str) -> None:
    res = subprocess.run(["nft", "-f", "-"], input=script, text=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"nft failed: {res.stderr.strip()}")


def allow_through_docker() -> None:
    """Docker sets the kernel's FORWARD policy to DROP, which would cut off every device routed
    through us. Its DOCKER-USER chain is the supported place to let our LAN's traffic through.
    Blocking still works: our own drop rules run before Docker's chains (priority -150)."""
    if subprocess.run(["nft", "list", "chain", "ip", "filter", "DOCKER-USER"],
                      capture_output=True).returncode != 0:
        return  # no Docker firewall on this host
    rules = subprocess.run(["nft", "-a", "list", "chain", "ip", "filter", "DOCKER-USER"],
                           capture_output=True, text=True).stdout
    for direction in ("iifname", "oifname"):
        if f'{direction} "{C.IFACE}"' in rules and "netwatch" in rules:
            continue
        res = subprocess.run(["nft", "insert", "rule", "ip", "filter", "DOCKER-USER", direction, C.IFACE,
                              "accept", "comment", '"netwatch: route the LAN"'], capture_output=True, text=True)
        if res.returncode != 0:
            log.warning("couldn't open Docker's firewall for %s: %s", C.IFACE, res.stderr.strip())


def setup(blocked_macs: list[str], force_dns: bool = False) -> None:
    _sysctl("/proc/sys/net/ipv4/ip_forward", "1")
    # Per-flow byte counters, which the dashboard reads from conntrack.
    _sysctl("/proc/sys/net/netfilter/nf_conntrack_acct", "1")
    # Traffic enters and leaves on the same port; without this the kernel
    # would tell devices "talk to the router directly" and we'd lose them.
    for iface in ("all", "default", C.IFACE):
        _sysctl(f"/proc/sys/net/ipv4/conf/{iface}/send_redirects", "0")

    elements = ", ".join(blocked_macs)
    _nft(f"""
table inet {TABLE}
delete table inet {TABLE}
table inet {TABLE} {{
  set blocked {{
    type ether_addr
    {f"elements = {{ {elements} }}" if elements else ""}
  }}
  chain prerouting {{
    type filter hook prerouting priority -150; policy accept;
    # Blocked devices keep LAN access (DNS, this dashboard) but lose the internet.
    iifname "{C.IFACE}" ether saddr @blocked ip daddr != {C.LAN} counter drop
  }}
  chain dns_nat {{
    type nat hook prerouting priority -100; policy accept;
  }}
  chain dotblock {{
    type filter hook forward priority 0; policy accept;
  }}
  chain postrouting {{
    type nat hook postrouting priority 110; policy accept;
    # Masquerade so replies come back through us (lets us see downloads too).
    oifname "{C.IFACE}" ip saddr {C.LAN} ip saddr != {C.SELF_IP} ip daddr != {C.LAN} masquerade
  }}
}}
""")
    set_dns_force(force_dns)
    allow_through_docker()
    log.info("nftables table '%s' installed (%d dropped, force_dns=%s)", TABLE, len(blocked_macs), force_dns)


def set_dns_force(on: bool) -> None:
    """Send every routed DNS query to Netwatch, and refuse DNS-over-TLS.

    Only affects devices routed through the gateway (not full-speed bypass).
    """
    script = f"flush chain inet {TABLE} dns_nat\nflush chain inet {TABLE} dotblock\n"
    if on:
        for proto in ("udp", "tcp"):
            script += (f'add rule inet {TABLE} dns_nat iifname "{C.IFACE}" ip saddr {C.LAN} '
                       f"ip daddr != {C.SELF_IP} {proto} dport 53 counter dnat ip to {C.SELF_IP}\n")
        # Android "Private DNS" (automatic mode) falls back to normal DNS when 853 is refused.
        script += (f'add rule inet {TABLE} dotblock iifname "{C.IFACE}" ip saddr {C.LAN} '
                   f"tcp dport 853 counter reject with tcp reset\n")
    _nft(script)


def sync_drop(macs: set[str], kill_ips: list[str] = ()) -> None:
    """Replace the drop set (blocked + pending approval) in one transaction."""
    elements = ", ".join(sorted(macs))
    _nft(f"flush set inet {TABLE} blocked\n"
         + (f"add element inet {TABLE} blocked {{ {elements} }}\n" if elements else ""))
    for ip in kill_ips:
        # Drop the device's existing connections so the block bites immediately.
        subprocess.run(["conntrack", "-D", "-s", ip], capture_output=True)


def teardown() -> None:
    subprocess.run(["nft", "delete", "table", "inet", TABLE], capture_output=True)
