import ipaddress

from app import config


def ranges(lan, self_ip, router_ip):
    return config.default_dhcp_ranges(ipaddress.ip_network(lan), self_ip, router_ip)


def test_dhcp_hands_out_the_lan_except_this_machine_and_the_router():
    assert ranges("192.168.1.0/24", "192.168.1.10", "192.168.1.1") == [
        ["192.168.1.2", "192.168.1.9"], ["192.168.1.11", "192.168.1.254"]]


def test_dhcp_skips_tiny_leftover_ranges():
    # .2-.3 between the router and this machine is too small to be worth handing out
    assert ranges("10.0.0.0/24", "10.0.0.4", "10.0.0.1") == [["10.0.0.5", "10.0.0.254"]]


def test_dhcp_on_a_big_network_stays_within_the_first_254_addresses():
    out = ranges("10.0.0.0/16", "10.0.0.2", "10.0.0.1")
    assert out == [["10.0.0.3", "10.0.0.254"]]


def test_dhcp_with_a_bad_address_still_works():
    assert ranges("192.168.1.0/24", "", "not-an-ip") == [["192.168.1.1", "192.168.1.254"]]


def test_settings_come_from_the_environment_in_tests():
    assert (config.IFACE, config.SELF_IP, config.ROUTER_IP, str(config.LAN)) == \
        ("lo", "192.168.1.10", "192.168.1.1", "192.168.1.0/24")
