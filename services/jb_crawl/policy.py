"""Deep-crawl metering: when a phone's deep crawl is due, and the tiny state
file that remembers it.

The user's rules (2026-10-11):
  - the core Songs sync (jb-source-phone) is untouched and runs on every
    connect; this only meters the DEEP crawl (playlists, artists, albums,
    genres)
  - remember only the LAST connect per phone, overwritten each time -- no
    growing log
  - a phone reconnecting on the same day doesn't start a new deep crawl
  - a deep crawl only runs while the jukebox is idle

So: the first connect of a day marks a crawl "due" for that phone. It stays
due (across later reconnects and restarts) until every branch has finished
in this cycle; an interrupted crawl resumes the branches it didn't finish at
the next idle window. Pure functions over a plain dict, no I/O except
load()/save().
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

STATE_PATH = Path("/var/lib/juk3-box/crawl-state.json")
BRANCHES = ("playlists", "artists", "albums", "genres")


def day(ts: float) -> str:
    """Local calendar day of a timestamp, e.g. '2026-10-11'."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def phone(state: dict, mac: str) -> dict:
    return state.setdefault(mac, {"last_connect": None, "due": False, "done": [], "last_complete": None})


def on_connect(state: dict, mac: str, now: float) -> bool:
    """Record a (re)connect. Returns whether this connect made a crawl due:
    only the first connect of a new day does."""
    p = phone(state, mac)
    first_today = p["last_connect"] is None or day(p["last_connect"]) != day(now)
    p["last_connect"] = now
    if first_today and not p["due"]:
        p["due"] = True
        p["done"] = []
        return True
    return False


def pending(state: dict, mac: str, enabled=BRANCHES) -> list:
    """Branches still to crawl in the current cycle, in order ([] = nothing due)."""
    p = phone(state, mac)
    if not p["due"]:
        return []
    return [b for b in enabled if b not in p["done"]]


def branch_done(state: dict, mac: str, branch: str, now: float, enabled=BRANCHES) -> bool:
    """Mark a branch stored. Returns True when that completes the cycle."""
    p = phone(state, mac)
    if branch not in p["done"]:
        p["done"].append(branch)
    return settle(state, mac, now, enabled)


def settle(state: dict, mac: str, now: float, enabled=BRANCHES) -> bool:
    """Close the cycle if nothing enabled is left to crawl -- also when the
    enabled branches changed after they were stored (otherwise the cycle
    stays open and the next day's connect never starts a new one).
    Returns True when it closed the cycle."""
    p = phone(state, mac)
    if p["due"] and not pending(state, mac, enabled):
        p["due"] = False
        p["last_complete"] = now
        return True
    return False


def is_idle(now: float, last_activity: float | None, playing: bool, connected_since: float | None,
            idle_s: float, settle_s: float) -> tuple[bool, str]:
    """The jukebox is idle when nothing plays, nobody has used the keypad or
    knob for idle_s, and the phone has been connected for settle_s (so the
    core sync is long done)."""
    if connected_since is None:
        return False, "no phone connected"
    if playing:
        return False, "music is playing"
    if now - connected_since < settle_s:
        return False, "phone just connected"
    if last_activity is not None and now - last_activity < idle_s:
        return False, "jukebox in use"
    return True, "idle"


def load(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(state: dict, path: Path = STATE_PATH) -> None:
    """Atomic replace, so a crash or power cut mid-write can't corrupt it."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, path)
