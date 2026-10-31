"""Build and drop: the shape of the last several seconds.

A beat detector lives in the present; a build-and-drop is a trajectory --
energy climbing for seconds (the riser), the low end falling away before the
drop (the breakdown), then bass slamming back (the drop). This keeps a short
memory of the fast layer and reports one of: none, rising, breakdown, drop.

Everything is judged against the song's own "groove" bass: the level its bass
sits at through the full sections over the last minute. A breakdown is the
bass well under that for a while; the drop is the bass back at (or above) the
groove -- not merely a stray note poking up out of the breakdown, which is
what fools a detector that compares against the quietest moment. Smoothed
bass takes a third of a second to climb back, so the drop is also called on
the kick itself: a beat whose own bass is right back at the groove
(SLAM_DB). That lands it on the beat, not half a second late (owner,
2026-10-30: the lights' flash came late); the smoothed check stays as the
fallback. Breakdowns
can run long (a whole thinned-out section, then a sneaky build), so they don't
time out quickly. Head-fakes still happen; hitting the obvious ones is the
goal.

Pure: feed it each frame's loudness, bass (dB), whether sound is present and
whether the frame is a beat.
"""

from collections import deque

import numpy as np

SMOOTH_S = 0.33          # bass is read as the mean over this long (kicks are spiky)
GROOVE_WINDOW_S = 60.0   # the groove: a high percentile of bass over this long
GROOVE_PCT = 80
BREAKDOWN_DB = 9.0       # bass this far under the groove, held BREAKDOWN_MIN_S = thinned out
BREAKDOWN_MIN_S = 1.0
DROP_DB = 5.0            # bass back within this of the groove = the drop
SLAM_DB = 3.0            # ...or a beat whose own bass is this close to it: the drop, at once
DROP_MIN_BREAKDOWN_S = 2.0
BREAKDOWN_MAX_S = 60.0   # never resolved: it was just a quiet section
DROP_HOLD_S = 2.5        # report "drop" this long, then settle
RISE_WINDOW_S = 6.0      # the riser: loudness climbing over this long
RISE_SLOPE = 0.025       # loudness units per second, sustained


class Trajectory:
    def __init__(self, fps: float):
        self.fps = fps
        self.level = deque(maxlen=int(RISE_WINDOW_S * fps))
        self.recent_bass = deque(maxlen=max(1, int(SMOOTH_S * fps)))
        self.history = deque(maxlen=int(GROOVE_WINDOW_S * fps))
        self.groove = None
        self.frame = 0
        self.low_frames = 0
        self.state = "none"
        self.state_frames = 0
        self.dropped = False     # set for the one frame a drop starts

    def _slope(self, values, seconds):
        k = int(seconds * self.fps)
        if len(values) < k:
            return 0.0
        y = np.array(list(values)[-k:])
        x = np.arange(k) / self.fps
        return float(np.polyfit(x, y, 1)[0])

    def step(self, level: float, bass_db: float, sounding: bool, beat: bool = False) -> str:
        self.dropped = False
        self.frame += 1
        self.state_frames += 1
        self.level.append(level)
        if not sounding:
            self.low_frames = 0
            return self._go("none")
        self.recent_bass.append(bass_db)
        bass = float(np.mean(self.recent_bass))
        self.history.append(bass)
        if self.groove is None or self.frame % int(self.fps / 2) == 0:   # twice a second is plenty
            self.groove = float(np.percentile(self.history, GROOVE_PCT))
        warmed_up = len(self.history) >= 4 * self.fps

        if self.state == "drop":
            if self.state_frames >= DROP_HOLD_S * self.fps:
                return self._go("none")
            return self.state
        if self.state == "breakdown":
            slam = beat and bass_db >= self.groove - SLAM_DB
            if slam or bass >= self.groove - DROP_DB:
                if self.state_frames >= DROP_MIN_BREAKDOWN_S * self.fps:
                    self.dropped = True
                    return self._go("drop")
                return self._go("none")                    # a brief dip, not a breakdown
            if self.state_frames > BREAKDOWN_MAX_S * self.fps:
                return self._go("none")
            return self.state

        # none / rising
        self.low_frames = self.low_frames + 1 if bass < self.groove - BREAKDOWN_DB else 0
        if warmed_up and self.low_frames >= BREAKDOWN_MIN_S * self.fps and level > 0.15:
            self.low_frames = 0
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
