"""A show's preview for admin › Light Show: the engine's own code plays it
to a built-in demo song, and the frames come back to be drawn on a
picture of the cabinet.

The demo song (DEMO): 120 bpm, a quiet intro, a verse, a build rising, a
breakdown, the drop, a big peak with a hit, and an outro -- every moment a
show reacts to. It goes through the real AudioFeed (as jb-analyzer's
messages would), the real director and the parts, and the accent chain's
brightness and current cap, so what's drawn is what the cabinet would do.

    render(style) -> {"fps", "seconds", "frames" (base64), "chains", "moments", "tiers"}
"""

import base64
import math

from services.jb_lights import accent, buttons, matrix
from services.jb_lights.audio_feed import N_BANDS, TOPIC, AudioFeed, LookPicker
from services.jb_lights.show.director import Director
from services.jb_lights.show.parts import ROW_X, AccentShow, BarShow, Keys
from services.jb_lights.themes.accent_city import BRIGHTNESS
from services.jb_lights.themes.accent_diner import limit

FPS = 15
SECONDS = 22.0
TEMPO = 120.0
# (from, to, part, level from, level to)
DEMO = [
    (0.0, 2.0, "intro", 0.25, 0.3),
    (2.0, 8.0, "verse", 0.5, 0.55),
    (8.0, 11.5, "build", 0.55, 0.8),
    (11.5, 12.0, "breakdown", 0.3, 0.3),
    (12.0, 19.0, "drop", 0.9, 0.85),
    (19.0, 22.0, "outro", 0.4, 0.25),
]
MOMENTS = [(2.0, "section", "up"), (8.0, "section", "up"), (12.0, "drop", None),
           (15.5, "hit", 0.9), (19.0, "section", "down")]
BUILD = {"build": "rising", "breakdown": "breakdown", "drop": "drop"}
BOTTOM_X = {buttons.SINGLE: 0.3, buttons.ALBUM: 0.5, buttons.RESET: 0.7}   # the third row, as drawn


class _Fixed:
    """The preview's library: always this show."""

    def __init__(self, style):
        self.style = style

    def pick(self, *a, **kw):
        return self.style

    def idle_show(self):
        return None


def _part(t):
    for a, b, name, l0, l1 in DEMO:
        if a <= t < b:
            return name, l0 + (l1 - l0) * (t - a) / (b - a)
    return "outro", 0.25


def _fast(t, prev_t):
    """jb-analyzer's fast frame for the demo at time t."""
    name, level = _part(t)
    beat_len = 60 / TEMPO
    beats_now, beats_before = int(t / beat_len), int(prev_t / beat_len)
    on_beat = beats_now != beats_before
    bar_beat = beats_now % 4
    drums = name not in ("intro", "breakdown")
    shimmer = [max(0.0, min(1.0, level * (1.1 - k / N_BANDS) + 0.1 * math.sin(t * 3 + k))) for k in range(N_BANDS)]
    msg = {"bands": shimmer, "level": level, "beat": on_beat and drums, "phase": (t / beat_len) % 1.0,
           "bar_beat": bar_beat, "build": BUILD.get(name, "none")}
    if on_beat and drums:
        msg["kick"] = 1.0 if name == "drop" or bar_beat in (0, 2) else 0.0
        msg["snare"] = 0.8 if bar_beat in (1, 3) else 0.0
    if name == "build" and int(t / (beat_len / 2)) != int(prev_t / (beat_len / 2)):
        msg["snare"] = 0.6 + 0.4 * (t - 8.0) / 3.5          # the roll tightening
    if drums and int(t / (beat_len / 2)) % 2 and int(t / (beat_len / 2)) != int(prev_t / (beat_len / 2)):
        msg["hat"] = 0.7
    return msg


def _rgb(px):
    if len(px) == 4:
        w = px[3]
        return tuple(min(255, c + w) for c in px[:3])
    return px


def render(style, fps: int = FPS, seconds: float = SECONDS) -> dict:
    feed = AudioFeed(picker=LookPicker("show"), director=Director(library=_Fixed(style)))
    apos = accent.positions()
    upper, bar, keys = AccentShow(apos), BarShow(), Keys()
    feed.on_message(f"{TOPIC}/slow", {"playing": True, "tempo": TEMPO, "mood": {"arousal": 0.65},
                                      "identity": {"title": "Demo song", "artist": "Juk3box"}}, now=0.0)
    out = bytearray()
    tiers, done = [], set()
    dt, prev = 1 / fps, -1 / fps
    n = int(seconds * fps)
    for f in range(n):
        t = f * dt
        for k, (at, kind, arg) in enumerate(MOMENTS):
            if k not in done and t >= at:
                done.add(k)
                data = {"type": kind}
                if kind == "section":
                    data["energy"] = arg
                elif kind == "hit":
                    data["strength"] = arg
                feed.on_message(f"{TOPIC}/events", data, now=t)
        feed.on_message(f"{TOPIC}/fast", _fast(t, prev), now=t)
        prev = t
        snap = feed.snapshot(now=t)
        a = limit(upper.frame(t, dt, snap), BRIGHTNESS)
        b = bar.frame(t, dt, snap)
        k = keys.frame(snap.cues, snap, dt)
        for px in a:
            out += bytes(px[:3])
        for px in b:
            out += bytes(int(v) for v in px[:3])
        for i in range(buttons.NUM_PIXELS):
            out += bytes(_rgb(k.get(i, (0, 0, 0, 0))))
        tiers.append((snap.cues.tier if snap.cues else "calm")[0])
    return {
        "fps": fps, "seconds": seconds, "frames": base64.b64encode(bytes(out)).decode(),
        "chains": [
            {"id": "accent", "pixels": accent.NUM_PIXELS, "positions": [[round(x, 4), round(y, 4)] for x, y in apos]},
            {"id": "levelbar", "pixels": matrix.NUM_PIXELS,
             "positions": [[round(x, 4), round(y, 4)] for x, y in matrix.positions()]},
            {"id": "buttons", "pixels": buttons.NUM_PIXELS,
             "keys": [[round(ROW_X.get(i, BOTTOM_X.get(i, 0.5)), 3), _key_row(i)] for i in range(buttons.NUM_PIXELS)]},
        ],
        "aspect": accent.UPPER_ASPECT,
        "moments": [{"t": a, "label": name} for a, _, name, _, _ in DEMO],
        "tiers": "".join(tiers),
    }


def _key_row(i: int) -> int:
    """The keypad as drawn: letters, then digits, then SINGLE / ALBUM / RESET."""
    if i in (buttons.SINGLE, buttons.ALBUM, buttons.RESET):
        return 2
    return 0 if i < 10 else 1
