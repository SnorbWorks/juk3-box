"""One frame of audio through every stage, in order: the fast layer, the
signal-level events, build/drop, the slow layer's inputs, the moments and
the beat clock. main.py feeds it the live capture; tools/analyze_song.py
feeds it a file, so tuning offline sees exactly what the cabinet sees.

Pure: no I/O.
"""

from services.jb_analyzer.dsp import FastAnalyzer
from services.jb_analyzer.levels import Levels
from services.jb_analyzer.moments import BeatClock, Moments
from services.jb_analyzer.slow import SlowAnalyzer
from services.jb_analyzer.trajectory import Trajectory


class Pipeline:
    def __init__(self, rate: int, hop: int):
        self.fast = FastAnalyzer(rate, hop)
        self.fps = fps = self.fast.fps
        self.levels, self.traj, self.slow = Levels(fps), Trajectory(fps), SlowAnalyzer(fps)
        self.moments, self.clock = Moments(fps), BeatClock(fps)

    def new_song(self):
        self.slow.reset()                              # its own tempo
        self.moments.reset()                           # its own loudness and sections

    def frame(self, samples) -> tuple:
        """(the fast-channel payload, or None in silence; [events])."""
        f = self.fast.process(samples)
        events = [{"type": e} for e in self.levels.step(f["_loudness"], f["peak"])]
        sounding = self.levels.sounding
        build = self.traj.step(f["_loudness"], f["_bass_db"], sounding, f["beat"])
        if self.traj.dropped:
            events.append({"type": "drop"})
        self.slow.step(f)
        found = self.moments.step(f["_rms_db"], f["_band_db"], sounding)
        if self.traj.dropped or any(e["type"] in ("hit", "section") for e in found):
            self.clock.downbeat()
        events += found
        phase, bar_beat = self.clock.step(self.slow.tempo, f["beat"])
        if not sounding:
            return None, events
        public = {k: v for k, v in f.items() if not k.startswith("_")}
        return {**public, "build": build, "phase": phase, "bar_beat": bar_beat}, events
