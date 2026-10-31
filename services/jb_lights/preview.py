"""A show's preview for admin › Light Show: the engine's own code plays it
to a built-in demo song, and the frames come back to be drawn on a
picture of the cabinet.

The demo song (DEMO) has every part a show plays its own way and every
moment it reacts to: a quiet intro, a sung verse, a pre-chorus building,
a gap, the drop into the chorus with a crash, the verse again (its look
again), the last chorus (bigger, in the show's peak colours) and the
outro. It plays as a kind of song (FEELS: upbeat, moody, a love song,
hard, 80s), at that kind's tempo, so the owner sees how the show plays to
it -- its colours, its pace, its accents. Everything goes through the
real AudioFeed (as jb-analyzer's messages would), the real director and
its plan, the parts, and the accent chain's brightness and current cap,
so what's drawn is what the cabinet would do.

    render(style, feel="upbeat") -> {"fps", "seconds", "frames" (base64), "chains", "aspect",
                                     "moments", "tiers", "feel", "feels", "tempo", "plan"}
"""

import base64
import math

from services.jb_lights import accent, buttons, matrix
from services.jb_lights.audio_feed import N_BANDS, TOPIC, AudioFeed, LookPicker
from services.jb_lights.show import plan as planner
from services.jb_lights.show.director import Director
from services.jb_lights.show.parts import ROW_X, AccentShow, BarShow, Keys
from services.jb_lights.themes.accent_city import BRIGHTNESS
from services.jb_lights.themes.accent_diner import limit

FPS = 15
SECONDS = 26.0
# The kinds of song a show can be previewed as: (name, tempo, the analyzer's feel for it).
FEELS = {
    "upbeat": ("Upbeat pop", 120.0, {"energy": 0.76, "valence": 0.85, "tender": 0.0, "aggressive": 0.12,
                                     "groove": 0.7, "moody": 0.05, "pace": 0.66, "voice": 0.75, "era": None,
                                     "words": ["groovy", "upbeat", "mid-tempo", "vocals"]}),
    "moody": ("Moody", 90.0, {"energy": 0.32, "valence": 0.3, "tender": 0.13, "aggressive": 0.1, "groove": 0.11,
                              "moody": 0.95, "pace": 0.23, "voice": 0.7, "era": None,
                              "words": ["moody", "slow", "vocals"]}),
    "love": ("Love song", 68.0, {"energy": 0.1, "valence": 0.6, "tender": 0.95, "aggressive": 0.05, "groove": 0.05,
                                 "moody": 0.2, "pace": 0.05, "voice": 0.8, "era": None,
                                 "words": ["love song", "slow", "vocals"]}),
    "hard": ("Hard rock", 136.0, {"energy": 0.95, "valence": 0.5, "tender": 0.0, "aggressive": 0.9, "groove": 0.3,
                                  "moody": 0.2, "pace": 0.85, "voice": 0.6, "era": None,
                                  "words": ["hard", "fast", "vocals"]}),
    "80s": ("80s", 118.0, {"energy": 0.62, "valence": 0.62, "tender": 0.0, "aggressive": 0.12, "groove": 0.3,
                           "moody": 0.17, "pace": 0.68, "voice": 0.6, "era": "80s",
                           "words": ["mid-tempo", "vocals", "80s"]}),
}
# (from s, to s, its label, role, size, voice, drums, level from, level to); label None: the gap
DEMO = [
    (0.0, 2.5, "A", "intro", 0.15, 0.1, 0.0, 0.25, 0.3),
    (2.5, 8.0, "B", "verse", 0.45, 0.85, 0.6, 0.5, 0.55),
    (8.0, 11.5, "C", "pre-chorus", 0.6, 0.7, 0.7, 0.55, 0.8),
    (11.5, 12.0, None, None, 0.0, 0.0, 0.0, 0.03, 0.03),
    (12.0, 17.5, "D", "chorus", 0.9, 0.8, 0.9, 0.9, 0.85),
    (17.5, 20.5, "B", "verse", 0.45, 0.85, 0.6, 0.5, 0.55),
    (20.5, 24.0, "D", "chorus", 1.0, 0.8, 0.9, 0.9, 0.9),
    (24.0, 26.0, "A", "outro", 0.15, 0.1, 0.0, 0.3, 0.2),
]
NAMES = {"intro": "Intro", "verse": "Verse", "pre-chorus": "Pre-chorus", "chorus": "Chorus", "outro": "Outro",
         None: "The gap"}
MOMENTS = [(11.5, "gap", None), (12.0, "drop", None), (12.0, "crash", 0.9), (14.75, "crash", 0.7),
           (20.5, "crash", 1.0), (22.25, "crash", 0.8)]
BOTTOM_X = {buttons.SINGLE: 0.3, buttons.ALBUM: 0.5, buttons.RESET: 0.7}   # the third row, as drawn


class _Fixed:
    """The preview's library: always this show."""

    def __init__(self, style):
        self.style = style

    def pick(self, *a, **kw):
        return self.style

    def choose(self, feel, *a, **kw):
        return self.style, planner.why(feel, None)

    def idle_show(self):
        return None


def _part(t):
    for k, (a, b, *_rest) in enumerate(DEMO):
        if a <= t < b:
            return k
    return len(DEMO) - 1


def _fast(t, prev_t, tempo, crash):
    """jb-analyzer's fast frame for the demo at time t."""
    a, b, label, role, size, voice, drums, l0, l1 = DEMO[_part(t)]
    level = l0 + (l1 - l0) * (t - a) / (b - a)
    beat_len = 60 / tempo
    beats_now, beats_before = int(t / beat_len), int(prev_t / beat_len)
    on_beat = beats_now != beats_before
    bar_beat = beats_now % 4
    half = int(t / (beat_len / 2)) != int(prev_t / (beat_len / 2))
    playing = drums > 0
    shimmer = [max(0.0, min(1.0, level * (1.1 - k / N_BANDS) + 0.1 * math.sin(t * 3 + k))) for k in range(N_BANDS)]
    msg = {"bands": shimmer, "level": level, "beat": on_beat and playing, "phase": (t / beat_len) % 1.0,
           "bar_beat": bar_beat, "build": "rising" if role == "pre-chorus" else "none"}
    if on_beat and playing:
        msg["kick"] = 1.0 if role == "chorus" or bar_beat in (0, 2) else 0.0
        msg["snare"] = 0.8 if bar_beat in (1, 3) else 0.0
    if role == "pre-chorus" and half:
        msg["snare"] = 0.6 + 0.4 * (t - a) / (b - a)          # the roll tightening
    if playing and half and int(t / (beat_len / 2)) % 2:
        msg["hat"] = 0.7
    if crash:
        msg["crash"], msg["crash_ago"] = crash, 0.0
        msg["ring"] = crash
    return msg


def _rgb(px):
    if len(px) == 4:
        w = px[3]
        return tuple(min(255, c + w) for c in px[:3])
    return px


def render(style, feel: str = "upbeat", fps: int = FPS, seconds: float = SECONDS) -> dict:
    feel = feel if feel in FEELS else "upbeat"
    _, tempo, the_feel = FEELS[feel]
    director = Director(library=_Fixed(style))
    feed = AudioFeed(picker=LookPicker("show"), director=director)
    apos = accent.positions()
    upper, bar, keys = AccentShow(apos), BarShow(), Keys()
    feed.on_message(f"{TOPIC}/slow", {"playing": True, "tempo": tempo, "mood": {"arousal": the_feel["energy"]},
                                      "identity": {"title": "Demo song", "artist": "Juk3box"},
                                      "feel": the_feel, "voice": the_feel["voice"]}, now=0.0)
    out = bytearray()
    tiers, done, seen, looks = [], set(), {}, {}
    dt, prev = 1 / fps, -1 / fps
    for f in range(int(seconds * fps)):
        t = f * dt
        k = _part(t)
        a, b, label, role, size, voice, drums, *_ = DEMO[k]
        if k not in done and label:
            done.add(k)
            seen[label] = seen.get(label, 0) + 1
            last = role == "chorus" and all(x[3] != "chorus" for x in DEMO[k + 1:])
            feed.on_message(f"{TOPIC}/events", {"type": "section", "source": "live", "label": label,
                                                "n": seen[label], "role": role, "size": size, "voice": voice,
                                                "drums": drums, "start": a, "at": t, "last": last}, now=t)
        crash = 0.0
        for j, (at, kind, arg) in enumerate(MOMENTS):
            if ("m", j) not in done and t >= at:
                done.add(("m", j))
                if kind == "crash":
                    crash = arg
                else:
                    feed.on_message(f"{TOPIC}/events", {"type": kind}, now=t)
        feed.on_message(f"{TOPIC}/fast", _fast(t, prev, tempo, crash), now=t)
        prev = t
        snap = feed.snapshot(now=t)
        cues = snap.cues
        if cues is not None and t >= (a + b) / 2 and k not in looks:
            looks[k] = cues.character
        frame = limit(upper.frame(t, dt, snap), BRIGHTNESS)
        for px in frame:
            out += bytes(px[:3])
        for px in bar.frame(t, dt, snap):
            out += bytes(int(v) for v in px[:3])
        lit = keys.frame(cues, snap, dt)
        for i in range(buttons.NUM_PIXELS):
            out += bytes(_rgb(lit.get(i, (0, 0, 0, 0))))
        tiers.append((cues.tier if cues else "calm")[0])
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
        "moments": [{"t": s[0], "label": NAMES.get(s[3], s[3]), "section": s[2], "character": looks.get(k)}
                    for k, s in enumerate(DEMO)],
        "tiers": "".join(tiers),
        "feel": feel, "feels": [[key, name] for key, (name, _, _) in FEELS.items()], "tempo": tempo,
        "plan": director.plan.summary(),
    }


def _key_row(i: int) -> int:
    """The keypad as drawn: letters, then digits, then SINGLE / ALBUM / RESET."""
    if i in (buttons.SINGLE, buttons.ALBUM, buttons.RESET):
        return 2
    return 0 if i < 10 else 1
