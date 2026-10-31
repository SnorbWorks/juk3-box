"""One frame of audio through every stage, in order: the fast layer, the
signal-level events, build/drop, the slow layer's inputs, the moments, the
beat clock, and the texture (harmony, timbre, the stereo picture) with the
key. main.py feeds it the live capture; tools/analyze_song.py and
tools/learn_song.py feed it a file, so tuning offline sees exactly what the
cabinet sees.

Pure: no I/O.
"""

import numpy as np

from services.jb_analyzer.dsp import FastAnalyzer
from services.jb_analyzer.levels import Levels
from services.jb_analyzer.moments import BeatClock, Moments
from services.jb_analyzer.slow import SlowAnalyzer
from services.jb_analyzer.texture import Key, Texture
from services.jb_analyzer.trajectory import Trajectory

TEMPO_EVERY_S = 2.0


class Pipeline:
    def __init__(self, rate: int, hop: int):
        self.fast = FastAnalyzer(rate, hop)
        self.fps = fps = self.fast.fps
        self.levels, self.traj, self.slow = Levels(fps), Trajectory(fps), SlowAnalyzer(fps)
        self.moments, self.clock = Moments(fps), BeatClock(fps)
        self.texture, self.key = Texture(rate, hop), Key()
        self.f = self.tex = None                       # this frame's fast layer (internals too) and texture
        self.tempo_every = max(1, round(TEMPO_EVERY_S * fps))
        self.n = 0

    def new_song(self):
        self.slow.reset()                              # its own tempo
        self.moments.reset()                           # its own loudness and sections
        self.key.reset()

    def frame(self, samples) -> tuple:
        """(the fast-channel payload, or None in silence; [events]).
        samples: one hop, stereo (n, 2) or mono (n,)."""
        samples = np.asarray(samples, dtype=np.float32)
        stereo = samples if samples.ndim == 2 else np.stack([samples, samples], axis=1)
        f = self.fast.process(stereo.mean(axis=1))
        self.f = f
        self.tex = self.texture.process(stereo, f["_power"])
        if self.tex is not None and self.levels.sounding:
            self.key.add(self.tex["chroma"], self.tex["tonal"] ** 0.5)
        events = [{"type": e} for e in self.levels.step(f["_loudness"], f["peak"])]
        sounding = self.levels.sounding
        build = self.traj.step(f["_loudness"], f["_bass_db"], sounding, f["beat"])
        if self.traj.dropped:
            events.append({"type": "drop"})
        self.slow.step(f)
        self.n += 1
        if self.n % self.tempo_every == 0:
            self.slow.update_tempo()                   # the slow channel reads it: state(estimate=False)
        found = self.moments.step(f["_rms_db"], f["_band_db"], sounding)
        if self.traj.dropped or any(e["type"] in ("hit", "section") for e in found):
            self.clock.downbeat()
        events += found
        phase, bar_beat = self.clock.step(self.slow.tempo, f["beat"])
        if not sounding:
            return None, events
        public = {k: v for k, v in f.items() if not k.startswith("_")}
        return {**public, "build": build, "phase": phase, "bar_beat": bar_beat}, events
