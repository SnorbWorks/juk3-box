"""The slow layer (local half): what kind of music this is, from the audio
alone -- tempo, sustained energy and brightness, busyness, and the mood they
add up to. No internet; works on any song from its first seconds.

Mood is two axes plus a convenience label (contract: audio-analysis v1):
  arousal  calm 0 .. energetic 1   (tempo, energy, busyness)
  warmth   bright/sharp 0 .. warm/dark 1   (sustained brightness)
It's a moving read over seconds, so it follows the song: a quiet bridge
pulls it down, the chorus brings it back.
"""

from collections import deque

import numpy as np

TEMPO_WINDOW_S = 8.0
MOOD_WINDOW_S = 10.0
BPM_LO, BPM_HI = 60.0, 180.0


class SlowAnalyzer:
    def __init__(self, fps: float):
        self.fps = fps
        self.onset_env = deque(maxlen=int(TEMPO_WINDOW_S * fps))
        n = int(MOOD_WINDOW_S * fps)
        self.levels = deque(maxlen=n)
        self.bright = deque(maxlen=n)
        self.beats = deque(maxlen=n)
        self.tempo = None

    def step(self, fast: dict):
        self.onset_env.append(fast["_flux"])
        self.levels.append(fast["level"])
        self.bright.append(fast["brightness"])
        self.beats.append(1 if fast["beat"] else 0)

    def _estimate_tempo(self):
        """Autocorrelation of the onset envelope over the last 8 s; the best
        lag between 60 and 180 bpm, refined between frames."""
        if len(self.onset_env) < self.onset_env.maxlen:
            return None
        x = np.array(self.onset_env, dtype=float)
        x = x - x.mean()
        if not np.any(x):
            return None
        ac = np.correlate(x, x, mode="full")[x.size - 1:]
        lo, hi = int(60 * self.fps / BPM_HI), int(60 * self.fps / BPM_LO) + 1
        lags = np.arange(lo, min(hi, ac.size - 1))
        if lags.size == 0:
            return None
        k = int(lags[np.argmax(ac[lags])])
        if ac[k] <= 0.15 * ac[0]:
            return None                                    # no steady pulse
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
        return 60 * self.fps / (k + shift)

    def state(self, playing: bool) -> dict:
        t = self._estimate_tempo()
        if t:
            self.tempo = t if self.tempo is None else self.tempo * 0.7 + t * 0.3
        energy = float(np.mean(self.levels)) if self.levels else 0.0
        brightness = float(np.mean(self.bright)) if self.bright else 0.0
        seconds = max(len(self.beats) / self.fps, 1e-6)
        busyness = float(np.clip(sum(self.beats) / seconds / 3.0, 0, 1))   # 3 beats/s = busy
        tempo_n = 0.5 if self.tempo is None else float(np.clip((self.tempo - 70) / 80, 0, 1))
        arousal = float(np.clip(0.4 * energy + 0.35 * tempo_n + 0.25 * busyness, 0, 1))
        warmth = float(np.clip(1 - brightness, 0, 1))
        if arousal < 0.5:
            label = "moody" if warmth >= 0.5 else "calm"
        else:
            label = "energetic" if warmth >= 0.5 else "bright"
        return {
            "playing": playing,
            "tempo": round(self.tempo, 1) if self.tempo else None,
            "energy": round(energy, 3),
            "brightness": round(brightness, 3),
            "busyness": round(busyness, 3),
            "mood": {"arousal": round(arousal, 3), "warmth": round(warmth, 3), "label": label},
        }

    def reset(self):
        """A new song (or silence): forget the last one's tempo."""
        self.onset_env.clear()
        self.tempo = None
