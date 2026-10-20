"""Which source plays a track, and whether it can right now.

The library mixes songs from the phone showing and from USB drives
(jb-library, "always merged"). A drive song's item_path is
"drive:<id>/<path>" and jb-source-local plays it; everything else is the
phone's (jb-source-phone). Pure, for tests.
"""

PHONE = "jb-source-phone"
LOCAL = "jb-source-local"


def source_of(track: dict | None) -> str:
    path = (track or {}).get("item_path")
    return LOCAL if isinstance(path, str) and path.startswith("drive:") else PHONE


def playable(track: dict | None, phone_ready: bool) -> bool:
    """A drive song is listed only while its drive is in, so it can play; a
    phone song needs the phone connected, with its song list read
    (jb-core's Queue.phone_ready)."""
    return source_of(track) == LOCAL or phone_ready


def cmd_topic(source: str) -> str:
    return f"jukebox/{source}/cmd"
