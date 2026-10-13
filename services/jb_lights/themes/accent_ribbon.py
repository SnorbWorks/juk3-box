"""accent-ribbon: the volume bar's theme 2 (the flowing light ribbon) on the
accent chain.

  - upper panel -> the ribbon flows across the panel's real shape (the
                   camera-made map): a glowing band rolling as a wave and
                   breathing thicker and thinner, lighting whatever columns,
                   circle and bars it passes over
  - footlights  -> the same band across their 4 rows (as the volume bar
                   runs it across its 5), in step with the panel's -- the
                   ribbon's glow spilling onto the grill
White-hot core, pink, purple, deep blue edge; hot spots flare and fade
along it, sparkles flick just outside it. Palette and hot-spot behaviour
come straight from volume_bar_theme2.

    sudo venv/bin/python -m services.jb_lights.themes.accent_ribbon [seconds]
"""

import math
import random
import signal
import sys
import time

from services.jb_lights import accent
from services.jb_lights.themes.accent_diner import limit
from services.jb_lights.themes.volume_bar_theme2 import HOT, SPARK, _lerp, _palette

FPS = 40
BRIGHTNESS = 0.6
BUCKETS = 36          # hot spots and shimmer along x, like the bar's 36 columns


class AccentRibbon:
    def __init__(self, pos):
        self.pos = pos
        self.bucket = [min(BUCKETS - 1, int(x * BUCKETS)) for x, _ in pos]
        self.hot = [0.0] * BUCKETS
        self.shimmer = [1.0] * BUCKETS
        self.sparks = {}

    def _step(self, ignite=None):
        self.hot = [h * 0.93 for h in self.hot]
        if (random.random() < 0.04) if ignite is None else ignite:
            c0, w = random.randrange(BUCKETS), random.randint(2, 4)
            for c in range(c0, min(BUCKETS, c0 + w)):
                self.hot[c] = 1.0
        self.shimmer = [s * 0.8 + (0.75 + 0.25 * random.random()) * 0.2 for s in self.shimmer]
        for i in list(self.sparks):
            self.sparks[i] *= 0.8
            if self.sparks[i] < 0.05:
                del self.sparks[i]

    @staticmethod
    def _wave(x, t):
        """Centre (0..1 of the height) and half-thickness of the band at x."""
        centre = 0.5 + 0.28 * math.sin(2 * math.pi * 0.9 * x - t * 1.1) + 0.08 * math.sin(2 * math.pi * 2.3 * x + t * 1.7)
        half = 0.22 + 0.08 * math.sin(t * 0.8 + x * 4.0) + 0.04 * math.sin(t * 2.3 - x * 7.0)
        return centre, half

    def _colour(self, i, d, c):
        colour = (0, 0, 0)
        if d < 1.15:
            k = min(1.0, (1.15 - d) / 0.4)
            colour = _palette(min(d, 1.0))
            if self.hot[c] > 0.05 and d < 0.6:
                colour = _lerp(colour, HOT, self.hot[c] * 0.85)
            colour = tuple(int(v * k * self.shimmer[c]) for v in colour)
        spark = self.sparks.get(i)
        return _lerp(colour, SPARK, spark) if spark else colour

    def frame(self, t, swell=1.0, ignite=None):
        """swell scales the band's thickness (the music's loudness when
        playing); ignite=True flares a hot spot (a beat) -- None = at random."""
        self._step(ignite)
        out = [(0, 0, 0)] * accent.NUM_PIXELS
        for i in range(accent.NUM_PIXELS):
            x, y = self.pos[i]
            centre, half = self._wave(x, t)
            half *= swell
            if i >= accent.UPPER:                      # footlights: rows 0..3 as the band's height
                row = (y - accent.FOOT_Y0) / accent.FOOT_DY
                y = row / (accent.FOOT_ROWS - 1)
            d = abs(y - centre) / max(half, 0.05)
            if 1.2 < d < 1.5 and random.random() < 0.002:
                self.sparks[i] = 1.0
            out[i] = self._colour(i, d, self.bucket[i])
        return out


def demo(seconds: float):
    from rpi_ws281x import Color, PixelStrip, ws
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    strip = PixelStrip(accent.NUM_PIXELS, accent.GPIO, 800000, 10, False, 255, 1, ws.WS2811_STRIP_GRB)
    strip.begin()
    ribbon = AccentRibbon(accent.positions())
    start = time.time()
    try:
        while time.time() - start < seconds:
            for i, (r, g, b) in enumerate(limit(ribbon.frame(time.time() - start), BRIGHTNESS)):
                strip.setPixelColor(i, Color(r, g, b))
            strip.show()
            time.sleep(1 / FPS)
    finally:
        for i in range(accent.NUM_PIXELS):
            strip.setPixelColor(i, Color(0, 0, 0))
        strip.show()
        strip.show()


if __name__ == "__main__":
    demo(float(sys.argv[1]) if len(sys.argv) > 1 else 60)
