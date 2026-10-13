"""accent-eq: the accent chain while music plays.

  - columns    -> a graphic EQ: the 16 columns (bubble tubes at idle), left
                  to right = bass to treble, each filling bottom to top with
                  white peak caps that hang, then fall -- the volume bar's
                  theme 3 behaviour, on columns of their own heights
  - logo bar   -> a steady warm glow (it lights the Rock-Ola logo), nudged
                  by the beat
  - circle     -> spins faster with the music's energy, pulses on the bass
  - the rest of the upper panel -> the colour wave, flowing faster with energy
  - footlights -> a warm wash that throbs with the bass

frame(levels, dt) takes 16 band levels 0..1 (bass first) -- fake audio for
now (the volume bar's), real PipeWire levels later.

    sudo venv/bin/python -m services.jb_lights.themes.accent_eq [seconds]
"""

import colorsys
import math
import random
import signal
import sys
import time

from services.jb_lights import accent
from services.jb_lights.themes.accent_diner import CIRCLE, Diner, limit

FPS = 40
BRIGHTNESS = 0.6
ATTACK, DECAY = 0.6, 0.15
PEAK_HOLD_S = 0.35
PEAK_GRAVITY = 2.0       # column heights / s^2
BANDS = 16


class EQ:
    def __init__(self, pos):
        shapes = Diner(pos)                       # same shape finding as the idle look
        self.pos, self.role, self.angle, self.bulb = pos, shapes.role, shapes.angle, shapes.bulb
        self.tubes = sorted(shapes.tubes, key=lambda t: sum(pos[i][0] for i in t["idx"]) / len(t["idx"]))
        n = len(self.tubes)
        self.level = [0.0] * n
        self.peak = [0.0] * n
        self.peak_t = [0.0] * n
        self.peak_v = [0.0] * n
        self.spin = 0.0
        self.flow = 0.0
        self.t = 0.0

    def frame(self, bands, dt):
        self.t += dt
        n = len(self.tubes)
        # Bands -> columns (resampled if the counts differ).
        target = [bands[min(len(bands) - 1, int(k * len(bands) / n))] for k in range(n)]
        bass = sum(bands[:3]) / 3
        energy = sum(bands) / len(bands)
        out = [(0.0, 0.0, 0.0)] * accent.NUM_PIXELS

        def rgb(h, s, v):
            return colorsys.hsv_to_rgb(h % 1.0, s, max(0.0, min(1.0, v)))

        for k, tube in enumerate(self.tubes):
            lv = self.level[k]
            lv += (target[k] - lv) * (ATTACK if target[k] > lv else DECAY)
            self.level[k] = lv
            if lv >= self.peak[k]:
                self.peak[k], self.peak_t[k], self.peak_v[k] = lv, self.t, 0.0
            elif self.t - self.peak_t[k] > PEAK_HOLD_S:
                self.peak_v[k] += PEAK_GRAVITY * dt
                self.peak[k] = max(lv, self.peak[k] - self.peak_v[k] * dt)
            idx = tube["idx"]                      # bottom -> top
            m = len(idx)
            fill = lv * m
            hue = 0.0 + 0.75 * k / max(1, n - 1)   # red bass .. violet treble
            cap = min(m - 1, int(self.peak[k] * m))
            for j, i in enumerate(idx):
                v = max(0.0, min(1.0, fill - j))   # the top pixel fades in
                out[i] = rgb(hue, 1.0, 0.07 + 0.93 * v)
            if self.peak[k] > 0.05:
                out[idx[cap]] = (1.0, 1.0, 1.0)

        for i in self.bulb:
            out[i] = rgb(0.09, 0.7, 0.55 + 0.35 * bass)
        self.spin += dt * (0.15 + 1.2 * energy)
        for i, a in self.angle.items():
            d = (a - self.spin * 2 * math.pi + math.pi) % (2 * math.pi) - math.pi
            out[i] = rgb(0.85, 0.6, 0.1 + 0.4 * bass + 0.5 * max(0.0, 1 - abs(d) / 0.9))
        self.flow += dt * (0.04 + 0.3 * energy)
        for i in range(accent.UPPER):
            if self.role[i] == "wave":
                x = self.pos[i][0]
                out[i] = rgb(0.55 + 0.2 * math.sin(2 * math.pi * (x - self.flow)), 0.9,
                             0.25 + 0.35 * energy)
        for i in range(accent.UPPER, accent.NUM_PIXELS):
            row = (self.pos[i][1] - accent.FOOT_Y0) / accent.FOOT_DY
            out[i] = rgb(0.07, 0.65, (0.18 + 0.55 * bass) * (1 - 0.15 * row))
        return [(int(r * 255), int(g * 255), int(b * 255)) for r, g, b in out]


def fake_bands(t):
    """The volume bar's fake audio, as 16 bands."""
    beat = max(0.0, math.sin(t * 2 * math.pi * 2.0)) ** 3      # ~120 BPM
    out = []
    for c in range(BANDS):
        x = c / (BANDS - 1)
        base = 0.5 - 0.25 * x
        wave = 0.28 * math.sin(t * 2.7 + c * 0.9) + 0.2 * math.sin(t * 4.9 - c * 1.7)
        out.append(max(0.0, min(1.0, base + wave + beat * (1 - x) * 0.6 + 0.3 * (random.random() - 0.5))))
    if random.random() < 0.1:
        out[random.randrange(BANDS)] = 1.0
    return out


def demo(seconds: float):
    from rpi_ws281x import Color, PixelStrip, ws
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    strip = PixelStrip(accent.NUM_PIXELS, accent.GPIO, 800000, 10, False, 255, 1, ws.WS2811_STRIP_GRB)
    strip.begin()
    eq = EQ(accent.positions())
    start = last = time.time()
    try:
        while time.time() - start < seconds:
            now = time.time()
            frame = eq.frame(fake_bands(now - start), now - last)
            last = now
            for i, (r, g, b) in enumerate(limit(frame, BRIGHTNESS)):
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
