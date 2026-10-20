"""The phones paired with the helper app, and pairing them.

A phone pairs once: the cabinet shows a 4-digit code, the owner types it in
the app, and the app gets a token it sends on every connect after. Only the
token's hash is kept. Pure apart from the JSON file, for tests.
"""

import hashlib
import json
import secrets
import time
from pathlib import Path

from shared.files import write_atomic

CODE_TTL_S = 120
TRIES = 3


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Phones:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.phones: dict = json.loads(path.read_text())
        except (OSError, ValueError):
            self.phones = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, json.dumps(self.phones, indent=1))

    def known(self, device_id, token) -> bool:
        p = self.phones.get(device_id) if isinstance(device_id, str) else None
        return bool(p and isinstance(token, str) and secrets.compare_digest(p["token"], _hash(token)))

    def device_for_token(self, token: str) -> str | None:
        h = _hash(token)
        return next((d for d, p in self.phones.items() if secrets.compare_digest(p["token"], h)), None)

    def add(self, device_id: str, name: str, model: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        self.phones[device_id] = {"name": name, "model": model, "token": _hash(token),
                                  "paired_at": time.time(), "last_seen": time.time()}
        self.save()
        return token

    def seen(self, device_id: str, name: str | None = None) -> None:
        p = self.phones.get(device_id)
        if p:
            p["last_seen"] = time.time()
            if name:
                p["name"] = name
            self.save()

    def forget(self, device_id: str) -> bool:
        if self.phones.pop(device_id, None) is None:
            return False
        self.save()
        return True

    def listing(self) -> list:
        return [{"device_id": d, "name": p.get("name"), "model": p.get("model"),
                 "paired_at": p.get("paired_at"), "last_seen": p.get("last_seen")}
                for d, p in sorted(self.phones.items(), key=lambda kv: -(kv[1].get("last_seen") or 0))]


class Challenge:
    """One phone's pairing code: right within TRIES and CODE_TTL_S, or a new code."""

    def __init__(self, device_id: str, name: str, now: float | None = None):
        self.device_id, self.name = device_id, name
        self.renew(now)

    def renew(self, now: float | None = None) -> None:
        self.code = f"{secrets.randbelow(10000):04d}"
        self.expires = (now if now is not None else time.time()) + CODE_TTL_S
        self.tries = TRIES

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires

    def check(self, code, now: float | None = None) -> bool:
        """True if right. Wrong: one try fewer (renew() when none are left)."""
        if self.expired(now):
            return False
        if isinstance(code, str) and secrets.compare_digest(code.strip(), self.code):
            return True
        self.tries -= 1
        return False
