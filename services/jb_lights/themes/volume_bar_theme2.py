"""volume_bar-theme2: flowing light ribbon.

Recreated from the user's reference video: a glowing horizontal band whose
center line undulates as a rolling wave and breathes thicker/thinner,
colored by distance from its center (white-hot core -> pink -> purple ->
deep blue edge -> dark), with red/orange hot spots that flare and fade,
occasional sparkles just outside the band, and a slight per-column shimmer.
With only 5 rows the band's center moves between rows fractionally (soft
edges), so the wave still looks fluid.

Ribbon.frame(t) renders one frame for time t (seconds). Running this module
plays the demo:

    python -m services.jb_lights.themes.volume_bar_theme2
"""

import math
import random
import time

from services.jb_lights import matrix
from services.jb_lights.ws2812_spi import WS2812SPI

FPS = 50
BRIGHTNESS = 0.60

# Palette by normalized distance from the band center (0 = core, 1 = edge).
PALETTE = [
    (0.00, (255, 235, 255)),  # white-hot core
    (0.35, (255, 70, 200)),   # pink / magenta
    (0.65, (150, 40, 255)),   # purple
    (1.00, (40, 30, 255)),    # deep blue edge
]
HOT = (255, 60, 30)        # red/orange hot spot
SPARK = (255, 140, 230)


def _lerp(a, b, k):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


def _palette(d, stops=PALETTE):
    for (d0, c0), (d1, c1) in zip(stops, stops[1:]):
        if d <= d1:
            return _lerp(c0, c1, (d - d0) / (d1 - d0))
    return stops[-1][1]


def colours(palette=None):
    """(the band's stops, its hot spots, its sparkles): the ribbon's own, or
    in a light show's colours -- a white-hot core, then the song's colours
    from the brightest to the darkest at the edge, hot spots in its warmest."""
    if not palette:
        return PALETTE, HOT, SPARK
    cs = sorted((tuple(int(v) for v in c) for c in palette), key=sum, reverse=True)
    core = _lerp(cs[0], (255, 255, 255), 0.6)
    if len(cs) == 1:
        cs = [cs[0], _lerp(cs[0], (0, 0, 0), 0.5)]
    stops = [(0.0, core)] + [(0.35 + 0.65 * k / (len(cs) - 1), c) for k, c in enumerate(cs)]
    hot = _lerp(max(cs, key=lambda c: c[0] - c[2]), (255, 255, 255), 0.2)
    return stops, hot, _lerp(cs[0], (255, 255, 255), 0.5)


class Ribbon:
    """Holds the effect's evolving state (hot spots, sparkles, shimmer)."""

    def __init__(self):
        self.hot = [0.0] * matrix.COLS
        self.shimmer = [1.0] * matrix.COLS
        self.sparks = {}  # (col, row) -> remaining intensity

    def _step_state(self, ignite=None):
        # Hot spots: occasionally (or on a beat) ignite a 2-4 column patch; everything decays.
        self.hot = [h * 0.93 for h in self.hot]
        if (random.random() < 0.04) if ignite is None else ignite:
            c0, w = random.randrange(matrix.COLS), random.randint(2, 4)
            for c in range(c0, min(matrix.COLS, c0 + w)):
                self.hot[c] = 1.0
        # Shimmer: smoothed per-column random wobble around 1.0.
        self.shimmer = [s * 0.8 + (0.75 + 0.25 * random.random()) * 0.2 for s in self.shimmer]
        for key in list(self.sparks):
            self.sparks[key] *= 0.8
            if self.sparks[key] < 0.05:
                del self.sparks[key]

    def frame(self, t, swell=1.0, ignite=None, palette=None):
        """swell scales the band's thickness (the music's loudness when
        playing); ignite=True flares a hot spot (a beat) -- None = at random;
        palette: a light show's colours (colours())."""
        self._step_state(ignite)
        stops, hot, spark_c = colours(palette)
        frame = matrix.blank()
        for c in range(matrix.COLS):
            x = c / (matrix.COLS - 1)
            # Rolling wave for the band's center (in rows; 2 = middle).
            center = (2.0 + 1.2 * math.sin(2 * math.pi * 0.9 * x - t * 1.1)
                      + 0.35 * math.sin(2 * math.pi * 2.3 * x + t * 1.7))
            # Breathing half-thickness, varying along the band.
            half = 1.25 + 0.45 * math.sin(t * 0.8 + x * 4.0) + 0.25 * math.sin(t * 2.3 - x * 7.0)
            half *= swell
            if random.random() < 0.01:  # sparkle just outside the band
                row = int(round(center + random.choice((-1, 1)) * (half + 0.8)))
                if 0 <= row < matrix.ROWS:
                    self.sparks[(c, row)] = 1.0
            for row in range(matrix.ROWS):
                d = abs(row - center) / max(half, 0.3)
                color = matrix.BLACK
                if d < 1.15:
                    k = min(1.0, (1.15 - d) / 0.4)  # soft edge fade
                    color = _palette(min(d, 1.0), stops)
                    if self.hot[c] > 0.05 and d < 0.6:
                        color = _lerp(color, hot, self.hot[c] * 0.85)
                    color = tuple(int(v * k * self.shimmer[c]) for v in color)
                spark = self.sparks.get((c, row))
                if spark:
                    color = _lerp(color, spark_c, spark)
                frame[matrix.index(c, row)] = color
        return frame


def demo():
    bar = WS2812SPI(matrix.NUM_PIXELS, brightness=BRIGHTNESS, max_amps=matrix.MAX_AMPS)
    ribbon = Ribbon()
    start = time.time()
    try:
        while True:
            bar.show(ribbon.frame(time.time() - start))
            time.sleep(1 / FPS)
    except KeyboardInterrupt:
        pass
    finally:
        bar.off()
        bar.close()


if __name__ == "__main__":
    demo()
