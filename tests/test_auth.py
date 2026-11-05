import re

import pytest

from app import auth


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """An Auth with its own empty data folder, like a brand-new install."""
    monkeypatch.setattr(auth, "_AUTH", tmp_path / "auth.json")
    monkeypatch.setattr(auth, "_SECRET", tmp_path / "secret")
    monkeypatch.setattr(auth, "INITIAL", tmp_path / "initial-password")
    monkeypatch.setattr(auth.C, "PASSWORD", "")
    return auth.Auth()


def test_a_new_install_gets_a_temporary_password(fresh):
    first = auth.INITIAL.read_text().strip()
    assert fresh.must_change
    assert fresh.check(first)
    assert not fresh.check("wrong")
    assert oct(auth.INITIAL.stat().st_mode & 0o777) == "0o600"  # only root can read it


def test_the_temporary_password_is_easy_to_type():
    for _ in range(50):
        pw = auth.generate_password()
        assert re.fullmatch(r"[a-km-z2-9]{4}-[a-km-z2-9]{4}-[a-km-z2-9]{4}", pw), pw
        assert not set(pw) & set("01lo")


def test_choosing_a_password_retires_the_temporary_one(fresh):
    first = auth.INITIAL.read_text().strip()
    fresh.set_password("my-own-password")
    assert not fresh.must_change
    assert not auth.INITIAL.exists()
    assert fresh.check("my-own-password") and not fresh.check(first)


def test_changing_the_password_signs_out_other_browsers(fresh):
    before = fresh.session_token()
    assert fresh.valid_session(before)
    fresh.set_password("my-own-password")
    assert not fresh.valid_session(before)
    assert fresh.valid_session(fresh.session_token())


def test_the_password_survives_a_restart(fresh):
    fresh.set_password("my-own-password")
    again = auth.Auth()
    assert again.check("my-own-password") and not again.must_change


def test_an_empty_session_is_never_valid(fresh):
    assert not fresh.valid_session("")
