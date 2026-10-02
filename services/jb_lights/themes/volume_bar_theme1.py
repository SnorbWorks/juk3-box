"""volume_bar-theme1: center-out rainbow spectrum analyzer.

Each column's level grows symmetrically up and down from the middle row, and
the outermost lit pixel fades with fractional levels so motion looks smoother
than the 4 discrete heights 5 rows allow (off / center / 3 tall / all 5).
Color is set by column: a rainbow from red (left) to magenta (right).

render(levels) is the reusable part -- levels are 0..1 per column, from a
real audio analyzer later. Running this module plays a demo driven by fake
audio (a wandering wave, a ~120 BPM pulse and random kicks):

    python -m services.jb_lights.themes.volume_bar_theme1
"""

import colorsys
import math
import random
import time

from services.jb_lights import matrix
from services.jb_lights.ws2812_spi import WS2812SPI

MID = matrix.ROWS // 2  # row 2 is the center line
FPS = 50
ATTACK, DECAY = 0.55, 0.12  # fraction of the gap closed per frame: rise fast, fall slower
BRIGHTNESS = 0.40

# Rainbow by column: hue 0.0 (red) -> 0.83 (magenta), full saturation.
COLUMN_COLORS = [
    tuple(int(v * 255) for v in colorsys.hsv_to_rgb(0.83 * c / (matrix.COLS - 1), 1.0, 1.0))
    for c in range(matrix.COLS)
]


def render(levels):
    """levels: one 0..1 value per column -> a full frame of pixels."""
    frame = matrix.blank()
    for c, lvl in enumerate(levels):
        reach = lvl * (MID + 1)  # 0..3: how far from center the column extends
        r, g, b = COLUMN_COLORS[c]
        for row in range(matrix.ROWS):
            k = max(0.0, min(1.0, reach - abs(row - MID)))  # fractional fade on the tip
            if k:
                frame[matrix.index(c, row)] = (int(r * k), int(g * k), int(b * k))
    return frame


def smooth(levels, targets):
    """Analyzer-style ballistics: jump up fast, fall back slower."""
    return [lv + (tg - lv) * (ATTACK if tg > lv else DECAY) for lv, tg in zip(levels, targets)]


def fake_spectrum(t):
    """Smooth, wandering per-column targets in 0..1 with random 'beats'."""
    beat = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 2.0)  # ~120 BPM pulse
    out = []
    for c in range(matrix.COLS):
        wave = (
            0.35 * math.sin(t * 3.1 + c * 0.45)
            + 0.25 * math.sin(t * 5.3 - c * 0.8)
            + 0.20 * math.sin(t * 1.7 + c * 0.17)
        )
        out.append(max(0.0, min(1.0, 0.45 + wave * 0.6 + 0.25 * beat * random.random())))
    # Occasional sharp kicks on random columns, like transients.
    if random.random() < 0.15:
        k = random.randrange(matrix.COLS)
        for c in range(max(0, k - 2), min(matrix.COLS, k + 3)):
            out[c] = min(1.0, out[c] + 0.5)
    # Light neighbor smoothing so it ripples like a waveform.
    last = matrix.COLS - 1
    return [(out[max(0, c - 1)] + 2 * out[c] + out[min(last, c + 1)]) / 4 for c in range(matrix.COLS)]


def demo():
    bar = WS2812SPI(matrix.NUM_PIXELS, brightness=BRIGHTNESS, max_amps=matrix.MAX_AMPS)
    levels = [0.0] * matrix.COLS
    start = time.time()
    try:
        while True:
            levels = smooth(levels, fake_spectrum(time.time() - start))
            bar.show(render(levels))
            time.sleep(1 / FPS)
    except KeyboardInterrupt:
        pass
    finally:
        bar.off()
        bar.close()


if __name__ == "__main__":
    demo()
