import pytest

from app import discovery


@pytest.mark.parametrize("registered, brand", [
    ("TP-LINK TECHNOLOGIES CO.,LTD.", "TP-Link"),
    ("NETGEAR", "Netgear"),
    ("ASUSTek COMPUTER INC.", "ASUSTek"),
    ("AVM Audiovisuelles Marketing und Computersysteme GmbH", "AVM"),
    ("zte corporation", "ZTE"),
    ("Sagemcom Broadband SAS", "Sagemcom"),
    ("Ubiquiti Inc", "Ubiquiti"),
    ("Huawei Technologies Co.,Ltd", "Huawei"),
    ("Private MAC", ""),
    ("", ""),
])
def test_router_brands_read_like_people_say_them(registered, brand):
    assert discovery.short_vendor(registered) == brand


def test_randomised_phone_macs_are_marked_private():
    assert discovery.vendor_of("02:11:22:33:44:55") == "Private MAC"
    assert discovery.vendor_of("da:a1:19:00:00:01") == "Private MAC"


@pytest.mark.parametrize("mac, ok", [
    ("a4:c3:f0:11:22:01", True),
    ("00:00:00:00:00:00", False),
    ("ff:ff:ff:ff:ff:ff", False),
    ("not-a-mac", False),
])
def test_valid_mac(mac, ok):
    assert discovery.valid_mac(mac) is ok
