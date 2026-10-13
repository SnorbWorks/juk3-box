"""The admin's one owner login: a password and the sessions it opens.

The password is kept only as a salted scrypt hash. A session is a random
token in an HttpOnly cookie; the server keeps only the token's SHA-256, so
the sessions file is no use to anyone who reads it. Repeated wrong
passwords lock that address out for a while.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from services.jb_admin.files import write_json

MIN_PASSWORD = 8
SESSION_DAYS = 30
# scrypt cost: ~50 ms and 16 MB on a Pi 4. Stored with each hash, so it
# can be raised later without breaking existing passwords.
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MAX_FAILURES = 5
LOCKOUT_S = 60


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, folder: Path, clock=time.time):
        self.folder = folder
        self.clock = clock
        self._failures: dict[str, list[float]] = {}

    @property
    def _auth_path(self) -> Path:
        return self.folder / "auth.json"

    @property
    def _sessions_path(self) -> Path:
        return self.folder / "sessions.json"

    def _read(self, path: Path) -> dict:
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # The password

    def has_password(self) -> bool:
        return "password" in self._read(self._auth_path)

    def set_password(self, password: str) -> None:
        if not isinstance(password, str) or len(password) < MIN_PASSWORD:
            raise ValueError(f"Use at least {MIN_PASSWORD} characters.")
        salt = os.urandom(16)
        entry = {"salt": salt.hex(), "hash": _hash(password, salt, **SCRYPT).hex(), **SCRYPT}
        write_json(self._auth_path, {"password": entry}, private=True)

    def check_password(self, password: str) -> bool:
        entry = self._read(self._auth_path).get("password")
        if not entry or not isinstance(password, str):
            return False
        try:
            got = _hash(password, bytes.fromhex(entry["salt"]), entry["n"], entry["r"], entry["p"])
            return hmac.compare_digest(got.hex(), entry["hash"])
        except (KeyError, ValueError, TypeError):
            return False

    # Guessing

    def locked_for(self, who: str) -> float:
        """Seconds until `who` (an address) may try again; 0 if now."""
        now = self.clock()
        recent = [t for t in self._failures.get(who, []) if now - t < LOCKOUT_S]
        self._failures[who] = recent
        if len(recent) < MAX_FAILURES:
            return 0.0
        return max(0.0, LOCKOUT_S - (now - recent[-MAX_FAILURES]))

    def failed(self, who: str) -> None:
        self._failures.setdefault(who, []).append(self.clock())

    def succeeded(self, who: str) -> None:
        self._failures.pop(who, None)

    # Sessions

    def _sessions(self) -> dict:
        now = self.clock()
        return {k: v for k, v in self._read(self._sessions_path).items()
                if isinstance(v, (int, float)) and v > now}

    def open_session(self) -> str:
        token = secrets.token_urlsafe(32)
        sessions = self._sessions()
        sessions[_digest(token)] = self.clock() + SESSION_DAYS * 86400
        write_json(self._sessions_path, sessions, private=True)
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        return _digest(token) in self._sessions()

    def close_session(self, token: str | None) -> None:
        sessions = self._sessions()
        if token and sessions.pop(_digest(token), None) is not None:
            write_json(self._sessions_path, sessions, private=True)

    def close_all(self) -> None:
        write_json(self._sessions_path, {}, private=True)
