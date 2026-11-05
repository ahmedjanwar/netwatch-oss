"""Shared setup for the tests.

Netwatch reads its configuration when it's imported, so the environment is set here, before any
`app` module loads: a throwaway data folder and a typical home network on the loopback interface
(no real network is touched).
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["NW_DATA"] = tempfile.mkdtemp(prefix="netwatch-test-")
os.environ.update({
    "NW_IFACE": "lo",
    "NW_SELF_IP": "192.168.1.10",
    "NW_ROUTER_IP": "192.168.1.1",
    "NW_LAN": "192.168.1.0/24",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))
