import ipaddress

from app import conntrack

LINE = ("tcp 6 431999 ESTABLISHED src=192.168.1.50 dst=142.250.74.110 sport=51234 dport=443 "
        "packets=10 bytes=1200 src=142.250.74.110 dst=203.0.113.7 sport=443 dport=51234 "
        "packets=12 bytes=56000 [ASSURED] mark=0 use=1 id=3141592")
LAN = ipaddress.ip_network("192.168.1.0/24")


def test_a_connection_line_is_parsed_with_both_directions():
    f = conntrack.parse_line(LINE)
    assert (f.src, f.dst, f.dport, f.up, f.down, f.fid) == ("192.168.1.50", "142.250.74.110", 443, 1200, 56000, "3141592")


def test_garbage_lines_are_ignored():
    assert conntrack.parse_line("") is None
    assert conntrack.parse_line("tcp 6 1 ESTABLISHED") is None


def test_internet_traffic_belongs_to_the_device_that_started_it():
    assert conntrack.owner_of(conntrack.parse_line(LINE), LAN, "192.168.1.10") == "192.168.1.50"


def test_traffic_between_home_devices_is_not_internet_traffic():
    local = LINE.replace("dst=142.250.74.110", "dst=192.168.1.20", 1)
    assert conntrack.owner_of(conntrack.parse_line(local), LAN, "192.168.1.10") is None
