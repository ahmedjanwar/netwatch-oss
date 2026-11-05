from app.adblock import clean_allowlist


def test_the_allowlist_accepts_what_people_paste():
    assert clean_allowlist("https://www.example.com/path, *.ads.example.org\nshop.example.net") == [
        "www.example.com", "ads.example.org", "shop.example.net"]


def test_the_allowlist_drops_junk_and_duplicates():
    assert clean_allowlist(["Example.com", "example.com", "not a domain", "", "x"]) == ["example.com"]
