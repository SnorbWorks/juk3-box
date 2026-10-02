"""volume_bar-theme3: classic spectrum analyzer with floating peak caps.

From the user's reference screenshot: one bar per column filling from the
bottom, colored by column in a rainbow that repeats twice across the width,
with a white peak pixel above each bar marking its recent high. The peak
holds briefly, then falls with gravity until it lands on the bar (or the bar
pushes it back up). Bars rise fast and fall slower, with a fractional fade on
the tip so motion looks smoother than 5 rows allow.

Analyzer.frame(levels, dt) takes 0..1 per column (from a real audio analyzer
later). Running this module plays a demo driven by fake audio (bass-weighted
waves, a ~120 BPM kick, random transients):

    python -m services.jb_lights.themes.volume_bar_theme3
"""

import colorsys
import math
import random
import time

from services.jb_lights import matrix
from services.jb_lights.ws2812_spi import WS2812SPI

FPS = 50
BRIGHTNESS = 0.60
RAINBOW_CYCLES = 2
ATTACK, DECAY = 0.6, 0.15     # fraction of the gap closed per frame: rise fast, fall slower
PEAK_HOLD_S = 0.35
PEAK_GRAVITY = 9.0            # rows / s^2
PEAK_COLOR = (255, 255, 255)

COLUMN_COLORS = [
    tuple(int(v * 255) for v in colorsys.hsv_to_rgb((RAINBOW_CYCLES * c / matrix.COLS) % 1.0, 1.0, 1.0))
    for c in range(matrix.COLS)
]


class Analyzer:
    """Holds bar levels and peak-cap physics between frames."""

    def __init__(self):
        n = matrix.COLS
        self.levels = [0.0] * n
        self.peaks = [0.0] * n      # peak height in rows (0..ROWS)
        self.peak_age = [0.0] * n   # seconds since the peak was last pushed up
        self.peak_vel = [0.0] * n   # falling speed, rows/s

    def frame(self, targets, dt):
        self.levels = [lv + (tg - lv) * (ATTACK if tg > lv else DECAY)
                       for lv, tg in zip(self.levels, targets)]
        frame = matrix.blank()
        for c in range(matrix.COLS):
            height = self.levels[c] * matrix.ROWS  # 0..5 rows, fractional
            # Peak: pushed up by the bar, otherwise holds, then falls with gravity.
            if height >= self.peaks[c]:
                self.peaks[c], self.peak_age[c], self.peak_vel[c] = height, 0.0, 0.0
            else:
                self.peak_age[c] += dt
                if self.peak_age[c] > PEAK_HOLD_S:
                    self.peak_vel[c] += PEAK_GRAVITY * dt
                    self.peaks[c] = max(height, self.peaks[c] - self.peak_vel[c] * dt)
            peak_row = min(matrix.ROWS - 1, int(self.peaks[c]))
            r, g, b = COLUMN_COLORS[c]
            for row in range(matrix.ROWS):
                k = max(0.0, min(1.0, height - row))  # fractional fill, tip fades
                color = (int(r * k), int(g * k), int(b * k))
                # Draw the cap only where the bar isn't already solid.
                if row == peak_row and self.peaks[c] > 0.15 and k < 0.9:
                    color = PEAK_COLOR
                frame[matrix.index(c, row)] = color
        return frame


def fake_spectrum(t):
    beat = max(0.0, math.sin(t * 2 * math.pi * 2.0)) ** 3  # ~120 BPM thump
    out = []
    for c in range(matrix.COLS):
        x = c / (matrix.COLS - 1)
        base = 0.55 - 0.25 * x  # a bit more energy in the bass (left)
        wave = 0.30 * math.sin(t * 2.7 + c * 0.9) + 0.22 * math.sin(t * 4.9 - c * 1.7)
        bass_kick = beat * (1.0 - x) * 0.6
        out.append(max(0.0, min(1.0, base + wave + bass_kick + 0.35 * (random.random() - 0.5))))
    if random.random() < 0.12:  # transients
        out[random.randrange(matrix.COLS)] = 1.0
    return out


def demo():
    bar = WS2812SPI(matrix.NUM_PIXELS, brightness=BRIGHTNESS, max_amps=matrix.MAX_AMPS)
    analyzer = Analyzer()
    start = last = time.time()
    try:
        while True:
            now = time.time()
            bar.show(analyzer.frame(fake_spectrum(now - start), now - last))
            last = now
            time.sleep(1 / FPS)
    except KeyboardInterrupt:
        pass
    finally:
        bar.off()
        bar.close()


if __name__ == "__main__":
    demo()
