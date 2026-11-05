"""The whole first-run experience through the real web app: sign in with the temporary password,
get sent to /welcome, choose a password, finish setup, and only then reach the dashboard."""
import pytest
from fastapi.testclient import TestClient

from app import auth


@pytest.fixture(scope="module")
def client():
    from app import main  # creates a fresh install in the test data folder
    # No `with`: the app's background tasks (dnsmasq, polling) aren't started.
    return TestClient(main.app, base_url="http://netwatch.test", follow_redirects=False)


NETWORK = {"iface": "lo", "self_ip": "192.168.1.10", "router_ip": "192.168.1.1",
           "lan": "192.168.1.0/24", "router_name": "Hallway router"}


def test_first_run(client):
    # Not signed in: pages send you to sign in, the API says no
    assert client.get("/").headers["location"] == "/login"
    assert client.get("/api/snapshot").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401

    # Signed in with the temporary password: everything but /welcome is locked
    first = auth.INITIAL.read_text().strip()
    assert client.post("/api/login", json={"password": first}).status_code == 200
    assert client.get("/").headers["location"] == "/welcome"
    assert client.get("/settings").headers["location"] == "/welcome"
    assert client.get("/api/snapshot").status_code == 403
    assert client.get("/welcome").status_code == 200

    state = client.get("/api/welcome/state").json()
    assert state["must_change_password"] and not state["setup_complete"]
    assert state["network"]["self_ip"] == "192.168.1.10"

    # Setup can't be finished before the password is changed
    assert client.post("/api/welcome/finish", json={"network": NETWORK}).status_code == 403

    # Choosing a password: too short, or the same as before, is refused
    assert client.post("/api/password", json={"new": "short"}).status_code == 400
    assert client.post("/api/password", json={"new": first}).status_code == 400
    assert client.post("/api/password", json={"new": "my-own-password"}).status_code == 200
    assert not auth.INITIAL.exists()

    # Still in setup: the dashboard stays locked
    assert client.get("/").headers["location"] == "/welcome"

    # Bad network settings and bad Telegram tokens are refused, with a reason
    bad = client.post("/api/welcome/finish", json={"network": NETWORK | {"router_ip": "10.0.0.1"}})
    assert bad.status_code == 400 and "isn't inside" in bad.json()["detail"]
    bad = client.post("/api/welcome/finish", json={"network": NETWORK | {"iface": "eth0; rm -rf /"}})
    assert bad.status_code == 400
    bad = client.post("/api/welcome/finish", json={"network": NETWORK, "telegram_token": "nope"})
    assert bad.status_code == 400

    # Finishing with the detected network (no restart needed) opens the dashboard
    done = client.post("/api/welcome/finish", json={"network": NETWORK, "adblock": True})
    assert done.status_code == 200 and done.json() == {"ok": True, "restarting": False}
    assert client.get("/").status_code == 200
    assert client.get("/api/snapshot").status_code == 200
    assert client.get("/api/welcome/state").json()["network"]["router_name"] == "Hallway router"

    # Signing in again now needs the new password
    client.post("/api/logout")
    client.cookies.clear()
    assert client.post("/api/login", json={"password": first}).status_code == 401
    assert client.post("/api/login", json={"password": "my-own-password"}).status_code == 200
