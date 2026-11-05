"""Dashboard password: scrypt hash in data/auth.json.

A fresh install has no password, so Netwatch makes one up, saves it to
data/initial-password (readable only by root, for the installer to print) and marks it
as temporary. The first sign-in then has to choose a new password before anything else.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
from pathlib import Path

from . import config as C

log = logging.getLogger("netwatch.auth")
_AUTH = Path(C.DATA) / "auth.json"
_SECRET = Path(C.DATA) / "secret"
INITIAL = Path(C.DATA) / "initial-password"
MIN_LENGTH = 8


def _hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def generate_password() -> str:
    """Easy to read and type from a terminal: three groups like k7qx-r92m-pa4t (no 0/o, 1/l)."""
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


class Auth:
    def __init__(self) -> None:
        Path(C.DATA).mkdir(parents=True, exist_ok=True)
        if not _SECRET.exists():
            _write_private(_SECRET, secrets.token_hex(32))
        self._secret = _SECRET.read_text().strip().encode()
        self._rec: dict = {}
        if _AUTH.exists():
            self._rec = json.loads(_AUTH.read_text())
        else:
            first = C.PASSWORD or generate_password()
            self.set_password(first, temporary=True)
            if not C.PASSWORD:
                _write_private(INITIAL, first + "\n")
            log.info("first run: a temporary dashboard password was created (run `sudo netwatch password` to see it)")

    @property
    def must_change(self) -> bool:
        return bool(self._rec.get("temporary"))

    def check(self, password: str) -> bool:
        if not self._rec:
            return False
        return hmac.compare_digest(_hash(password, self._rec["salt"]), self._rec["hash"])

    def set_password(self, password: str, temporary: bool = False) -> None:
        salt = secrets.token_hex(16)
        self._rec = {"salt": salt, "hash": _hash(password, salt)} | ({"temporary": True} if temporary else {})
        _write_private(_AUTH, json.dumps(self._rec))
        if not temporary:
            INITIAL.unlink(missing_ok=True)  # the printed password no longer works

    def session_token(self) -> str:
        # Bound to the password hash, so changing the password signs out every
        # other browser.
        return hmac.new(self._secret, f"netwatch:{self._rec.get('hash', '')}".encode(), hashlib.sha256).hexdigest()

    def valid_session(self, token: str) -> bool:
        return bool(self._rec) and hmac.compare_digest(token or "", self.session_token())
