import pytest

from app import services


@pytest.mark.parametrize("domain, service", [
    ("rr3---sn-abc.googlevideo.com", "YouTube"),
    ("www.youtube.com", "YouTube"),
    ("ipv4-c001.1.oca.nflxvideo.net", "Netflix"),
    ("example.co.uk", "example.co.uk"),
    ("api.sub.example.com", "example.com"),
    (None, "Unknown (no DNS)"),
])
def test_domains_become_services_people_recognise(domain, service):
    assert services.classify(domain) == service
