"""Setup mode's files: how jb-admin (the setup pages, as admin) and
jb-netwatch (the hotspot, as root) talk (docs/Setup Wizard — Plan.md,
"Setup mode").

jb-netwatch writes the state; jb-admin and jb-core drop requests into
requests/, one JSON file each, which jb-netwatch reads and deletes. The
folder belongs to admin (0700, made by deploy.sh), so only admin and root
see the hotspot's password or a Wi-Fi password on its way through.
"""

import json
import os
import secrets
import time
from pathlib import Path

from shared.files import write_atomic

DIR = Path("/var/lib/juk3-box/setupnet")
ACTIONS = ("start", "stop", "scan", "join")
SSID_MAX_BYTES = 32


def state_path(folder: Path = DIR) -> Path:
    return Path(folder) / "state.json"


def requests_dir(folder: Path = DIR) -> Path:
    return Path(folder) / "requests"


def read_state(folder: Path = DIR) -> dict:
    """{"active": False} when setup mode isn't on (or nothing's written yet)."""
    try:
        data = json.loads(state_path(folder).read_text())
    except (OSError, ValueError):
        return {"active": False}
    return data if isinstance(data, dict) else {"active": False}


def request(action: str, folder: Path = DIR, **details) -> None:
    """Ask jb-netwatch for something: start, stop, scan, or join
    (ssid, password, hidden)."""
    if action not in ACTIONS:
        raise ValueError(action)
    name = f"{time.time():.6f}-{secrets.token_hex(4)}.json"
    write_atomic(requests_dir(folder) / name, json.dumps({"action": action, **details}), mode=0o600)


def take_requests(folder: Path = DIR) -> list[dict]:
    """The waiting requests, oldest first, each removed as it's read.
    Anything unreadable is dropped."""
    out = []
    try:
        names = sorted(p for p in requests_dir(folder).iterdir() if p.suffix == ".json" and not p.name.startswith("."))
    except OSError:
        return out
    for p in names:
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            data = None
        try:
            os.unlink(p)
        except OSError:
            pass
        if isinstance(data, dict) and data.get("action") in ACTIONS:
            out.append(data)
    return out


def valid_join(req: dict) -> str | None:
    """Why a join request can't be tried, or None if it can."""
    ssid, password = req.get("ssid"), req.get("password", "")
    if not isinstance(ssid, str) or not ssid or len(ssid.encode()) > SSID_MAX_BYTES:
        return "Pick a network."
    if not isinstance(password, str):
        return "That password isn't right."
    if password and not 8 <= len(password) <= 63:
        return "Wi-Fi passwords are 8 to 63 characters."
    if any(ord(c) < 32 for c in ssid + password):
        return "That has a character a network name can't have."
    return None

