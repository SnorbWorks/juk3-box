"""Signal-level events: silence (bottom threshold) and clipping (top).

  silence  the level stays near zero for SILENCE_S -> "silence"; when it
           comes back -> "sound". A free trigger for anything that wants to
           know a song ended or paused, without asking the phone.
  clip     the signal pins near full scale CLIP_FRAMES times within half a
           second -> "clip" (at most every CLIP_COOLDOWN_S): distortion,
           and speaker strain.
Pure: feed it each frame's level and peak at `fps`; it returns event types.
"""

SILENCE_LEVEL = 0.06      # level (0..1 over -60..0 dB A-weighted): ~-56 dB
SILENCE_S = 1.5
CLIP_PEAK = 0.98
CLIP_FRAMES = 3
CLIP_COOLDOWN_S = 5.0


class Levels:
    def __init__(self, fps: float):
        self.fps = fps
        self.sounding = False
        self.quiet_frames = 0
        self.clip_frames = []
        self.frame = 0
        self.last_clip = -1e9

    def step(self, level: float, peak: float) -> list:
        self.frame += 1
        events = []
        if level < SILENCE_LEVEL:
            self.quiet_frames += 1
            if self.sounding and self.quiet_frames >= SILENCE_S * self.fps:
                self.sounding = False
                events.append("silence")
        else:
            self.quiet_frames = 0
            if not self.sounding:
                self.sounding = True
                events.append("sound")
        if peak >= CLIP_PEAK:
            self.clip_frames.append(self.frame)
        self.clip_frames = [f for f in self.clip_frames if self.frame - f < 0.5 * self.fps]
        if len(self.clip_frames) >= CLIP_FRAMES and self.frame - self.last_clip >= CLIP_COOLDOWN_S * self.fps:
            self.last_clip = self.frame
            events.append("clip")
        return events
