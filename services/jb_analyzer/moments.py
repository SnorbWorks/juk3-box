"""Moments: the things a lighting operator waits for.

  gap      mid-song, the sound suddenly falls away (the half-bar of silence
           before a drop, a band stopping dead) -> "gap"
  hit      a big attack: the sound slamming back after a gap, or a jump to
           full loudness out of something quieter (a crash, the drop
           landing) -> "hit", with how strong and whether a gap came first
  section  the song has moved on: the last few seconds sound clearly
           different from the stretch before (verse -> chorus, the bridge)
           -> "section", with whether the energy went up or down
And the beat clock: the tempo turned into a steady count the lights can
move to -- where we are within the beat (phase 0..1) and which beat of a
4-beat bar -- kept in step by the detected kicks, and carried through the
gaps where no kick lands.

Everything is judged against the song's own recent sound, so it works at
any volume. Pure: feed it one frame at a time at `fps`.
"""

from collections import deque

import numpy as np

# gap / hit
REF_S = 3.0              # "how loud the song is right now": the median of this long
GAP_DB = 14.0            # this far under it...
GAP_FRAMES = 3           # ...for this many frames (~0.1 s) = a gap
GAP_MAX_S = 1.4          # longer than this is silence, not a gap (levels.py says so)
GAP_REARM_S = 0.5        # a stutter of gaps is one gap
WARMUP_S = 4.0           # no gaps or hits in a song's first seconds
HIT_JUMP_DB = 8.0        # a hit: this much louder than the typical level of...
HIT_LOOKBACK_S = 0.4     # ...the moment before, which itself sat at least
HIT_DIP_DB = 7.0         # this far under the song (a punchy beat in a loud mix isn't a hit),
HIT_NEAR_DB = 4.0        # landing within this of the song's loudness
HIT_COOLDOWN_S = 1.0
HIT_AFTER_GAP_S = 0.3    # a hit this soon after a gap ends is the gap's payoff
# section
SECTION_EVERY = 6        # frames between section checks (5 a second at 30 fps)
SECTION_NOW_S = 4.0      # the last few seconds...
SECTION_BEFORE_S = 8.0   # ...against the stretch before them
SECTION_SCORE = 0.65     # how different (in the song's own spreads) counts as a new section
SECTION_MIN_S = 10.0     # sections last at least this long
COARSE = 8               # bands averaged down to this many for comparing
# beat clock
PULL = 0.3               # how hard a detected kick pulls the clock into step
SNAP_AFTER = 3           # this many kicks in a row all off the same way: jump to them


class Moments:
    def __init__(self, fps: float):
        self.fps = fps
        self.rms = deque(maxlen=int(REF_S * fps))
        self.recent = deque(maxlen=max(2, int(HIT_LOOKBACK_S * fps)))
        self.frame = 0
        self.sounding_frames = 0
        self.low_frames = 0
        self.in_gap = False
        self.faded = False
        self.gap_ref = None
        self.gap_end = -1e9
        self.last_hit = -1e9
        self.ref = None
        # sections: one coarse snapshot every SECTION_EVERY frames
        self.snaps = deque(maxlen=int((SECTION_NOW_S + SECTION_BEFORE_S + 30) * fps / SECTION_EVERY))
        self.last_section = 0

    def reset(self):
        """A new song: its own loudness, its own sections."""
        self.__init__(self.fps)

    def step(self, rms_db: float, band_db, sounding: bool) -> list:
        """Events this frame: dicts with at least "type"."""
        self.frame += 1
        if not sounding:
            self.sounding_frames, self.low_frames, self.in_gap, self.faded = 0, 0, False, False
            self.rms.clear()
            self.recent.clear()
            return []
        self.sounding_frames += 1
        events = []
        warmed = self.sounding_frames >= WARMUP_S * self.fps

        if not self.in_gap and self.rms and (self.ref is None or self.frame % 5 == 0):
            self.ref = float(np.median(self.rms))      # the gap itself mustn't drag this down
        ref = self.ref if self.ref is not None else rms_db
        quiet_before = float(np.median(self.recent)) if self.recent else rms_db

        if rms_db >= ref - GAP_DB / 2:
            self.faded = False
        if self.in_gap:
            if rms_db > self.gap_ref - GAP_DB / 2:
                self.in_gap, self.low_frames, self.gap_end = False, 0, self.frame
            elif self.low_frames > GAP_MAX_S * self.fps:
                # The song fading out (levels.py will call it silence): no
                # more gaps until the sound is properly back.
                self.in_gap, self.faded = False, True
            else:
                self.low_frames += 1
        elif (warmed and not self.faded and rms_db < ref - GAP_DB
              and self.frame - self.gap_end >= GAP_REARM_S * self.fps):
            self.low_frames += 1
            if self.low_frames >= GAP_FRAMES:
                self.in_gap, self.gap_ref = True, ref
                events.append({"type": "gap"})
        else:
            self.low_frames = 0
            jump = rms_db - quiet_before
            if (warmed and jump >= HIT_JUMP_DB and quiet_before <= ref - HIT_DIP_DB and rms_db >= ref - HIT_NEAR_DB
                    and self.frame - self.last_hit >= HIT_COOLDOWN_S * self.fps):
                events.append(self._hit(jump, after_gap=self.frame - self.gap_end <= HIT_AFTER_GAP_S * self.fps))
        if not self.in_gap:
            self.rms.append(rms_db)
        self.recent.append(rms_db)

        if self.frame % SECTION_EVERY == 0:
            coarse = np.array([np.mean(c) for c in np.array_split(np.asarray(band_db), COARSE)] + [rms_db])
            self.snaps.append(coarse)
            change = self._section_change()
            if change is not None:
                events.append(change)
        return events

    def _hit(self, jump, after_gap):
        self.last_hit = self.frame
        strength = float(np.clip((jump - HIT_JUMP_DB / 2) / 20, 0.3, 1.0))
        return {"type": "hit", "strength": round(strength, 2), "after_gap": after_gap}

    def _section_change(self):
        per_s = self.fps / SECTION_EVERY
        now_n, before_n = int(SECTION_NOW_S * per_s), int(SECTION_BEFORE_S * per_s)
        if len(self.snaps) < now_n + before_n:
            return None
        if (self.frame - self.last_section) < SECTION_MIN_S * self.fps:
            return None
        s = np.array(self.snaps)
        now, before = s[-now_n:], s[-(now_n + before_n):-now_n]
        spread = np.maximum(s.std(axis=0), 1.5)            # this song's own swing, per band (dB)
        diff = (now.mean(axis=0) - before.mean(axis=0)) / spread
        score = float(np.sqrt(np.mean(diff ** 2)))
        if score < SECTION_SCORE:
            return None
        self.last_section = self.frame
        energy = diff[-1] * spread[-1]                     # loudness change in dB
        return {"type": "section", "change": round(min(score / 1.5, 1.0), 2),
                "energy": "up" if energy > 2 else "down" if energy < -2 else "same"}


class BeatClock:
    """The tempo as a running count, kept in step by the kicks."""

    def __init__(self, fps: float):
        self.fps = fps
        self.phase = 0.0          # 0 on the beat .. 1 just before the next
        self.count = 0            # beats so far; count % 4 is the beat in the bar
        self.off = []             # how far the last few kicks were from the clock's beat

    def step(self, tempo: float | None, beat: bool) -> tuple:
        """(phase, beat in the bar 0..3), or (None, None) without a tempo."""
        if not tempo:
            self.off.clear()
            return None, None
        self.phase += tempo / 60 / self.fps
        if self.phase >= 1:
            self.phase -= 1
            self.count += 1
        if beat:
            err = self.phase if self.phase < 0.5 else self.phase - 1    # kick early (<0) or late (>0) vs the clock
            self.off = (self.off + [err])[-SNAP_AFTER:]
            if len(self.off) == SNAP_AFTER and all(abs(e) > 0.2 for e in self.off) \
                    and max(self.off) - min(self.off) < 0.2:
                self._land()                                            # consistently off: the kicks are right
            else:
                self.phase -= PULL * err
                if self.phase < 0:
                    self.phase += 1
                    self.count -= 1
                elif self.phase >= 1:
                    self.phase -= 1
                    self.count += 1
        return round(self.phase, 3), self.count % 4

    def downbeat(self):
        """A big moment (a hit, a new section) almost always lands on the
        bar's first beat: count from there."""
        if self.phase > 0.5:
            self._land()
        self.count -= self.count % 4

    def _land(self):
        if self.phase > 0.5:
            self.count += 1
        self.phase = 0.0
        self.off.clear()
