"""The phone's Songs list between connections, so a reconnect checks a few
songs instead of reading them all again (2026-11-01).

Reading every song over Bluetooth takes ~33 s for ~900 songs, and just
getting to the Songs list takes 20 of them: on BlueZ 5.82 (the Pi 5's
Raspberry Pi OS) every folder change is followed by a song-count request
iOS never answers, and the next request waits out its 10 s timeout
(measured 2026-11-01: 10.15 s, then 0.03 s for the same call). That was
the "Connecting and updating library" overlay, every time the phone
connected. But an iPhone's song IDs (the item<N> that ends a play path)
survive a reconnect, its Songs list keeps its order until songs are added
or removed, and the Songs folder's own path under the player doesn't
change. So a reconnect shows the list from last time at once, and checks
it behind the scenes: the same number of songs, and the same song at a
few places spread through the list. Anything else gets a full read.

A song can only be played once BlueZ has listed it in this connection.
A pick of a song that hasn't been listed yet lists just that song first,
by its place in the list, and checks the ID.

Pure except load/save/forget, which keep one small file per phone in
SONGS_DIR (the data partition, so it survives restarts): the songs, and
where Songs sits under the player ("/Filesystem/item<N>/item<N>").
"""

import json
import re
import time
from pathlib import Path

from shared.files import write_atomic

SONGS_DIR = Path("/var/lib/juk3-box/phone-songs")
SPOT_CHECKS = 5           # places checked on a reconnect, first and last included
DETAILS = ("artist", "album", "track_number", "duration_ms")

_UID = re.compile(r"/item(\d+)$")


def uid_of(item_path: str | None) -> str | None:
    """The phone's ID for a song: the number in the play path's last item<N>."""
    m = _UID.search(item_path or "")
    return m.group(1) if m else None


def folder_of(item_path: str) -> str:
    """The folder part of a play path: where this connection's songs live."""
    return item_path.rsplit("/", 1)[0]


def _value(v):
    """A D-Bus Variant's value (or a plain value, in tests)."""
    return getattr(v, "value", v)


def track_from_item(path: str, props: dict) -> dict:
    """One listed song: its play path and title, and the details the phone
    sent with it (BlueZ 5.82 asks for them). A detail the phone left out
    is left out here too, never stored as empty."""
    meta = {k: _value(v) for k, v in (_value(props.get("Metadata")) or {}).items()}
    title = _value(props.get("Name")) or meta.get("Title") or None
    track = {"item_path": path, "title": title}
    for key, out in (("Artist", "artist"), ("Album", "album")):
        text = meta.get(key)
        if isinstance(text, str) and text.strip():
            track[out] = text.strip()
    for key, out in (("TrackNumber", "track_number"), ("Duration", "duration_ms")):
        number = meta.get(key)
        if isinstance(number, int) and number > 0:
            track[out] = number
    return track


def is_folder(props: dict) -> bool:
    return _value(props.get("Type")) == "folder"


def spot_indices(count: int, checks: int = SPOT_CHECKS) -> list[int]:
    """Where to check a list of `count` songs: the first, the last and
    evenly in between. Songs added or removed change the count; the checks
    are for the rarer swap (one added, one removed), which shifts every
    song between the two. A pick of a song that moved is caught anyway:
    its ID is checked when it's listed (see the module notes)."""
    if count <= 0:
        return []
    if count <= checks:
        return list(range(count))
    step = (count - 1) / (checks - 1)
    return sorted({round(i * step) for i in range(checks)})


def same_song(stored: dict, listed: dict) -> bool:
    """A stored song and one just listed at its place: the same ID and title."""
    return stored.get("uid") is not None and stored.get("uid") == uid_of(listed.get("item_path")) \
        and stored.get("title") == listed.get("title")


def rebased(stored: list[dict], folder: str) -> list[dict]:
    """The stored list with play paths for this connection's Songs folder."""
    out = []
    for s in stored:
        t = {"item_path": f"{folder}/item{s['uid']}", "title": s["title"]}
        t.update({k: s[k] for k in DETAILS if s.get(k) is not None})
        out.append(t)
    return out


def stored_form(tracks: list[dict]) -> list[dict]:
    """What's kept of a full read: each song's ID, title and details (play
    paths name this connection's player, so they aren't kept)."""
    out = []
    for t in tracks:
        uid = uid_of(t.get("item_path"))
        if uid is None or not t.get("title"):
            return []          # not a list we can check against later
        s = {"uid": uid, "title": t["title"]}
        s.update({k: t[k] for k in DETAILS if t.get(k) is not None})
        out.append(s)
    return out


def positions(tracks: list[dict]) -> dict[str, int]:
    """Song ID -> place in the list (the first, should an ID repeat)."""
    out: dict[str, int] = {}
    for i, t in enumerate(tracks):
        uid = uid_of(t.get("item_path"))
        if uid is not None:
            out.setdefault(uid, i)
    return out


def _path(mac: str, store: Path) -> Path:
    return store / (mac.replace(":", "-") + ".json")


def under_player(item_path: str, player_path: str) -> str | None:
    """Where a song's folder sits under its player ("/Filesystem/item<N>/
    item<N>"): the part of its path that's the same in every connection."""
    where = folder_of(item_path)
    return where[len(player_path):] if where.startswith(player_path + "/") else None


def save(mac: str, tracks: list[dict], player_path: str, now: float | None = None,
         store: Path = SONGS_DIR) -> bool:
    """Keep a full read's list for the next reconnect. Returns whether it was kept."""
    songs = stored_form(tracks)
    where = under_player(tracks[0]["item_path"], player_path) if songs else None
    if not songs or where is None:
        return False
    store.mkdir(parents=True, exist_ok=True)
    write_atomic(_path(mac, store), json.dumps(
        {"at": now if now is not None else time.time(), "under_player": where, "songs": songs}))
    return True


def load(mac: str, store: Path = SONGS_DIR) -> dict:
    """The kept list: {"songs": [...], "under_player": "/Filesystem/..."},
    empty ([] and None) when there's none or it can't be read."""
    try:
        doc = json.loads(_path(mac, store).read_text())
        songs, where = doc.get("songs"), doc.get("under_player")
    except (OSError, ValueError, AttributeError):
        songs, where = None, None
    if not isinstance(songs, list) or not isinstance(where, str):
        return {"songs": [], "under_player": None}
    return {"songs": songs, "under_player": where}


def forget(mac: str, store: Path = SONGS_DIR) -> None:
    """A phone forgotten at the jukebox: its list goes too."""
    try:
        _path(mac, store).unlink()
    except OSError:
        pass
