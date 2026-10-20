"""Which source plays a track, and whether it can right now.

The library mixes songs from the phone showing and from USB drives
(jb-library, "always merged"). A drive song's item_path is
"drive:<id>/<path>" and jb-source-local plays it; a song from the juk3box
Helper app's library is "helper:<device>/<item>" and jb-source-helper plays
it; everything else is the Bluetooth phone's (jb-source-phone). Pure, for
tests.
"""

PHONE = "jb-source-phone"
LOCAL = "jb-source-local"
HELPER = "jb-source-helper"
# Sources that play exactly one song and stop (no auto-advance to fight):
# leaving them is "stop", not "pause".
ONE_SONG = (LOCAL, HELPER)


def source_of(track: dict | None) -> str:
    path = (track or {}).get("item_path")
    if isinstance(path, str) and path.startswith("drive:"):
        return LOCAL
    if isinstance(path, str) and path.startswith("helper:"):
        return HELPER
    return PHONE


def playable(track: dict | None, phone_ready: bool, helper_ready: bool = False) -> bool:
    """A drive song is listed only while its drive is in, so it can play; a
    phone song needs the phone connected, with its song list read
    (jb-core's Queue.phone_ready); the app's songs, the app reachable
    (Queue.helper_ready: connected, or asleep but able to be woken)."""
    source = source_of(track)
    if source == LOCAL:
        return True
    return helper_ready if source == HELPER else phone_ready


def cmd_topic(source: str) -> str:
    return f"jukebox/{source}/cmd"
