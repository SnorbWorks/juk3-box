"""The helper app's messages (docs/contracts/helper-protocol.md), and what
the app's library becomes on the bus. Pure, for tests."""

import json
import re
import uuid
from datetime import datetime

VERSION = 1
PREFIX = "helper:"
ARTWORK_KEY_RE = re.compile(r"^[0-9a-f]{40}$")


def message(type_: str, payload: dict | None = None, id_: str | None = None) -> str:
    msg = {"v": VERSION, "type": type_, "payload": payload or {}}
    if id_:
        msg["id"] = id_
    return json.dumps(msg, ensure_ascii=False)


def new_id() -> str:
    return str(uuid.uuid4())


def parse(text: str) -> dict | None:
    """A message from the app, or None for anything that isn't one."""
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return None
    if not isinstance(msg.get("payload"), dict):
        msg["payload"] = {}
    return msg


def item_path(device_id: str, item_id: str) -> str:
    return f"{PREFIX}{device_id}/{item_id}"


def split_item_path(path) -> tuple[str, str] | None:
    """"helper:<device>/<item>" -> (device, item)."""
    if not isinstance(path, str) or not path.startswith(PREFIX):
        return None
    device, _, item = path[len(PREFIX):].partition("/")
    return (device, item) if device and item else None


def _epoch(iso) -> float | None:
    if not isinstance(iso, str):
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def track(device_id: str, song: dict) -> dict | None:
    """An app library song as a bus track (jb-source-local's shape, plus
    added_at and artwork_key)."""
    if not isinstance(song, dict) or not song.get("id") or not song.get("title"):
        return None
    duration = song.get("duration")
    key = song.get("artworkKey")
    return {
        "title": str(song["title"]),
        "artist": song.get("artist") or None,
        "album": song.get("album") or None,
        "genre": song.get("genre") or None,
        "album_track": song.get("trackNumber") if isinstance(song.get("trackNumber"), int) else None,
        "duration_ms": int(duration * 1000) if isinstance(duration, (int, float)) else None,
        "year": song.get("year") if isinstance(song.get("year"), int) else None,
        "added_at": _epoch(song.get("dateAdded")),
        "artwork_key": key if isinstance(key, str) and ARTWORK_KEY_RE.match(key) else None,
        "item_path": item_path(device_id, str(song["id"])),
    }


def playlists(items: list, songs: list) -> list:
    """App playlists as the bus's {name, titles} (jb-library keeps playlists by title)."""
    title_of = {s["id"]: s["title"] for s in songs if isinstance(s, dict) and s.get("id") and s.get("title")}
    out = []
    for p in items:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        titles = [title_of[i] for i in p.get("songIds") or [] if i in title_of]
        out.append({"name": str(p["name"]), "titles": titles})
    return out


def now_playing(device_id: str, state: dict | None) -> dict:
    """playback_state in the phone's now-playing shape (jb-source-phone)."""
    if not state or state.get("state") not in ("playing", "paused"):
        return {"status": "stopped", "position_ms": None, "title": None, "artist": None, "album": None,
                "genre": None, "duration_ms": None, "track_number": None, "number_of_tracks": None}

    def ms(v):
        return int(v * 1000) if isinstance(v, (int, float)) else None

    return {
        "status": state["state"],
        "position_ms": ms(state.get("position")),
        "title": state.get("title"), "artist": state.get("artist"), "album": state.get("album"),
        "genre": None, "duration_ms": ms(state.get("duration")),
        "track_number": None, "number_of_tracks": None,
        "item_path": item_path(device_id, str(state["itemId"])) if state.get("itemId") else None,
    }
