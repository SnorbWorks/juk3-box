"""Made-up songs for the tests: sections of chords and drums, at a tempo.

    song([("A", 8), ("B", 8), ("A", 8), ("B", 8)])     -> (stereo samples, rate)

Each kind of section has its own chord (no notes shared, so their harmony
is clearly different), its own sound (soft sines, or a brighter buzz) and
loudness; a kick on 1 and 3, a snare on 2 and 4 and eighth-note hats where
it has drums. The bass walks the chord's notes beat by beat, so the harmony
moves within a bar the way a real song's does.
"""

import numpy as np

RATE = 48000
NOTE = {"C": 261.63, "C#": 277.18, "D": 293.66, "D#": 311.13, "E": 329.63, "F": 349.23, "F#": 369.99,
        "G": 392.00, "G#": 415.30, "A": 440.00, "A#": 466.16, "B": 493.88}
KINDS = {
    #       chord              amp   bright drums
    "A": (("C", "E", "G"), 0.18, False, True),
    "B": (("F#", "A#", "C#"), 0.32, True, True),
    "C": (("D", "F", "A"), 0.14, False, False),
    "D": (("G#", "B", "D#"), 0.28, True, True),
}


def _tone(freq, n, rate, bright):
    t = np.arange(n) / rate
    if not bright:
        return np.sin(2 * np.pi * freq * t)
    return sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 6)) * 0.7


def _kick(n, rate):
    t = np.arange(n) / rate
    f = 50 + 70 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / rate) * np.exp(-t * 18)


def _noise(n, rnd, decay, hp=False):
    x = rnd.standard_normal(n)
    if hp:
        x = np.diff(x, prepend=0.0)
    return x * np.exp(-np.arange(n) / RATE * decay)


def song(plan, bpm=120.0, rate=RATE, seed=1, kinds=None) -> tuple:
    kinds = kinds or KINDS
    rnd = np.random.default_rng(seed)
    beat = int(round(60 / bpm * rate))
    out = []
    for kind, bars in plan:
        notes, amp, bright, drums = kinds[kind]
        freqs = [NOTE[x] for x in notes]
        for _ in range(bars):
            for b in range(4):
                x = np.zeros(beat)
                env = np.minimum(1.0, np.arange(beat) / (0.01 * rate)) * np.exp(-np.arange(beat) / rate * 1.5)
                for f in freqs:
                    x += _tone(f, beat, rate, bright) * env * 0.3
                x += _tone(freqs[b % 3] / 2, beat, rate, bright) * env * 0.4      # the bass walks the chord
                if drums:
                    k = min(beat, int(0.25 * rate))
                    if b in (0, 2):
                        x[:k] += _kick(k, rate) * 1.2
                    if b in (1, 3):
                        x[:k] += _noise(k, rnd, 25) * 0.35
                    for h in (0, beat // 2):
                        m = min(beat - h, int(0.04 * rate))
                        x[h:h + m] += _noise(m, rnd, 90, hp=True) * 0.08
                out.append(x * amp)
    mono = np.concatenate(out).astype(np.float32)
    mono += (rnd.standard_normal(mono.size) * 1e-4).astype(np.float32)
    return np.stack([mono, mono * 0.9], axis=1), rate


def section_starts(plan, bpm=120.0) -> list:
    """Where each section of a plan starts, in seconds."""
    out, t = [], 0.0
    for _, bars in plan:
        out.append(t)
        t += bars * 4 * 60 / bpm
    return out


def frames(stereo, hop=1600):
    """(t at the end of each hop, the hop)."""
    for s in range(0, stereo.shape[0] - hop + 1, hop):
        yield (s + hop) / RATE, stereo[s:s + hop]


PLAN = [("A", 8), ("B", 8), ("A", 8), ("B", 8), ("C", 4), ("B", 8)]
_heard = {}


def heard(plan=None, bpm=120.0, seed=1):
    """A made-up song listened to the way the cabinet listens (no ears):
    (the Listening, its section events as (t, event), the recording's arrays
    as the study reads them, the samples). Kept, so the tests share one run."""
    import json

    from services.jb_analyzer.listening import Listening
    plan = tuple(plan or PLAN)
    key = (plan, bpm, seed)
    if key not in _heard:
        x, rate = song(plan, bpm=bpm, seed=seed)
        li = Listening(rate, 1600, None)
        li.new_song({"title": "made up", "artist": "the tests", "duration": x.shape[0] / rate}, 0.0)
        events = []
        for i, (t, hop) in enumerate(frames(x)):
            _, ev = li.frame(hop, t)
            if i % 60 == 0:
                li.position(t, t)
            events += [(t, e) for e in ev if e["type"] == "section"]
        rec = li.recording.arrays()
        for k in ("genres", "events", "identity"):
            rec[k] = json.loads(str(rec[k]))
        rec["fps"] = float(rec["fps"])
        _heard[key] = (li, events, rec, x)
    return _heard[key]
