"""Build and drop: the shape of the last several seconds.

A beat detector lives in the present; a build-and-drop is a trajectory --
energy climbing for seconds (the riser), the low end falling away right
before the drop (the breakdown), then bass slamming back (the drop). This
keeps a short memory of the fast layer and reports one of: none, rising,
breakdown, drop. Head-fakes happen (a quiet verse into a chorus looks like a
small build); catching most and hitting the obvious ones hard is the goal.

Pure: feed it each frame's level, bass (dB) and brightness at `fps`.
"""

from collections import deque

import numpy as np

RISE_WINDOW_S = 6.0      # the riser: level climbing over this long
RISE_SLOPE = 0.025       # level units per second, sustained
BREAKDOWN_DB = 9.0       # bass this far under its recent average = thinned out
BREAKDOWN_MIN_S = 0.5
DROP_DB = 8.0            # bass this far back above the breakdown = the drop
DROP_HOLD_S = 2.5        # report "drop" this long, then settle


class Trajectory:
    def __init__(self, fps: float):
        self.fps = fps
        n = int(12 * fps)
        self.level = deque(maxlen=n)
        self.bass = deque(maxlen=n)
        self.state = "none"
        self.state_frames = 0
        self.breakdown_bass = None
        self.dropped = False     # set for the one frame a drop starts

    def _slope(self, values, seconds):
        k = int(seconds * self.fps)
        if len(values) < k:
            return 0.0
        y = np.array(list(values)[-k:])
        x = np.arange(k) / self.fps
        return float(np.polyfit(x, y, 1)[0])

    def step(self, level: float, bass_db: float, sounding: bool) -> str:
        self.dropped = False
        self.level.append(level)
        self.bass.append(bass_db)
        self.state_frames += 1
        if not sounding:
            return self._go("none")
        recent_bass = float(np.mean(list(self.bass)[-int(4 * self.fps):]))

        if self.state == "drop":
            if self.state_frames >= DROP_HOLD_S * self.fps:
                return self._go("none")
            return self.state
        if self.state == "breakdown":
            if bass_db > self.breakdown_bass + DROP_DB:
                self.dropped = True
                return self._go("drop")
            if self.state_frames > 16 * self.fps:          # never resolved: it was a head-fake
                return self._go("none")
            self.breakdown_bass = min(self.breakdown_bass, bass_db)
            return self.state
        # none / rising
        older = list(self.bass)[:-int(1 * self.fps)] or [bass_db]
        bass_avg = float(np.mean(older[-int(8 * self.fps):]))
        if bass_db < bass_avg - BREAKDOWN_DB and len(self.bass) > 4 * self.fps:
            # Thinned out: bass gone while the level holds up somewhat.
            thin = list(self.bass)[-int(BREAKDOWN_MIN_S * self.fps):]
            if all(b < bass_avg - BREAKDOWN_DB for b in thin) and level > 0.15:
                self.breakdown_bass = bass_db
                return self._go("breakdown")
        if self._slope(self.level, RISE_WINDOW_S) > RISE_SLOPE and level > 0.2:
            return self._go("rising")
        if self.state == "rising" and self._slope(self.level, 2.0) < 0:
            return self._go("none")
        return self.state if self.state == "rising" else "none"

    def _go(self, state):
        if state != self.state:
            self.state, self.state_frames = state, 0
        return state
