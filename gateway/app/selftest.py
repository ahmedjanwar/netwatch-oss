"""Pre-deploy self-test. Run inside the NEW image in a throwaway network namespace:

    docker run --rm --network none --cap-add NET_ADMIN <image> python -m app.selftest

Applies the real nftables rules (to the sandbox namespace only), imports every
module and renders a report chart. Exits non-zero on any failure.
"""
import os
import sys
import tempfile

os.environ.setdefault("NW_DATA", tempfile.mkdtemp())
# The sandbox has no network to detect, so give it a typical home LAN.
for k, v in {"NW_IFACE": "eth0", "NW_SELF_IP": "192.168.1.10", "NW_ROUTER_IP": "192.168.1.1",
             "NW_LAN": "192.168.1.0/24"}.items():
    os.environ.setdefault(k, v)


def main() -> int:
    import importlib
    # Every module must at least import (a typo anywhere fails the self-test, not the user's install).
    for name in ("adblock", "conntrack", "engine", "insights", "services", "setup", "store", "telegram"):
        importlib.import_module(f"app.{name}")
    from app import charts, discovery, dnslog, netfilter
    netfilter.setup(["02:00:00:00:00:01"], force_dns=True)
    netfilter.set_dns_force(False)
    netfilter.set_dns_force(True)
    netfilter.sync_drop({"02:00:00:00:00:02", "02:00:00:00:00:03"})
    netfilter.sync_drop(set())
    netfilter.teardown()
    args = dnslog.build_args()
    assert args[0] == "dnsmasq" and any(a.startswith("--addn-hosts=") for a in args), args
    fake = {"totals": {"down": 1, "up": 1}, "daily": [{"day": "2026-01-01", "down": 5, "up": 1}],
            "hourly": [{"hour": 0, "down": 5, "up": 1}], "devices": [],
            "capacity": {"peak_bps": 0, "saturated_minutes": 0},
            "health": {"internet": {"avg_rtt": None}, "outages": []}}
    assert charts.report_png(fake, "7d")[:4] == b"\x89PNG"
    # first run: a temporary password that must be changed
    from app import auth, config
    a = auth.Auth()
    first = auth.INITIAL.read_text().strip()
    assert a.must_change and a.check(first) and not a.check("wrong"), "temporary password"
    a.set_password("a-new-password")
    assert not a.must_change and not auth.INITIAL.exists() and a.check("a-new-password"), "password change"
    # DHCP hands out the LAN without this machine and the router
    import ipaddress
    ranges = config.default_dhcp_ranges(ipaddress.ip_network("192.168.1.0/24"), "192.168.1.10", "192.168.1.1")
    assert ranges == [["192.168.1.2", "192.168.1.9"], ["192.168.1.11", "192.168.1.254"]], ranges
    assert discovery.short_vendor("TP-LINK TECHNOLOGIES CO.,LTD.") == "TP-Link"
    assert discovery.short_vendor("Private MAC") == ""
    print("selftest OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
