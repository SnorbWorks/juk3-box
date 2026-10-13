"""accent-diner: an idle look for the accent chain, after the classic jukebox.

  - columns    -> bubble tubes: each glows its own colour, bright bubbles
                  drift up at their own pace (the Wurlitzer signature)
  - top bar    -> theatre marquee: warm bulbs chasing along it
  - circle     -> a record spinning: one highlight orbiting it
  - everything else on the upper panel (bottom strips, swooshes, short
                  strips) -> a slow colour wave flowing left to right
  - footlights -> a warm wash on the grill, brightest at the front, with
                  a gentle shimmer
The palette drifts slowly over a couple of minutes.

Shapes are found from the camera-made map (accent.positions()): the chain
is cut wherever it jumps or turns back, and each piece is classed by its
shape. The circle is pixels 77-85 (its chain range from the mapping).

    sudo venv/bin/python -m services.jb_lights.themes.accent_diner [seconds]
"""

import colorsys
import math
import random
import signal
import sys
import time

from services.jb_lights import accent

FPS = 40
BRIGHTNESS = 0.55
CIRCLE = range(76, 85)      # chain indices of the circle (pixels 77-85)
AMPS_PER_CHANNEL_FULL = 0.020


def segments(pos):
    """Cut the upper panel's chain into straight-ish pieces: at jumps and
    wherever it doubles back. -> [[index, ...], ...]"""
    upper = pos[:accent.UPPER]
    steps = [math.dist(a, b) for a, b in zip(upper, upper[1:])]
    med = sorted(steps)[len(steps) // 2]
    segs, cur = [], [0]
    for i in range(1, accent.UPPER):
        jump = steps[i - 1] > 1.8 * med
        turn = False
        if len(cur) >= 2:
            # A turn of more than ~60 degrees (sideways to the next column
            # counts, not just doubling back).
            (ax, ay), (bx, by), (cx, cy) = upper[cur[-2]], upper[cur[-1]], upper[i]
            ux, uy, vx, vy = bx - ax, by - ay, cx - bx, cy - by
            turn = ux * vx + uy * vy < 0.5 * math.hypot(ux, uy) * math.hypot(vx, vy)
            # Leaving a column sideways (to the end of the next one) is a cut too.
            turn = turn or (abs(uy) > 2 * abs(ux) and abs(vx) * accent.UPPER_ASPECT > abs(vy))
        if jump or turn:
            segs.append(cur)
            cur = []
        cur.append(i)
    segs.append(cur)
    return segs


class Diner:
    def __init__(self, pos):
        self.pos = pos
        self.role = ["wave"] * accent.NUM_PIXELS
        self.tubes = []           # (indices sorted bottom->top, hue, speed, bubbles)
        rnd = random.Random(7)
        for seg in segments(pos):
            seg = [i for i in seg if i not in CIRCLE]
            if not seg:
                continue
            xs = [pos[i][0] for i in seg]
            ys = [pos[i][1] for i in seg]
            if max(ys) < 0.06:
                for i in seg:
                    self.role[i] = "marquee"
            elif len(seg) >= 4 and (max(ys) - min(ys)) > 2.5 * (max(xs) - min(xs)):
                order = sorted(seg, key=lambda i: -pos[i][1])          # bottom first
                hue = (len(self.tubes) * 0.13) % 1.0
                self.tubes.append({"idx": order, "hue": hue, "speed": rnd.uniform(0.10, 0.18),
                                   "phase": rnd.random(), "lo": min(ys), "hi": max(ys)})
                for i in seg:
                    self.role[i] = "tube"
        for i in CIRCLE:
            self.role[i] = "circle"
        for i in range(accent.UPPER, accent.NUM_PIXELS):
            self.role[i] = "foot"
        cx = sum(pos[i][0] for i in CIRCLE) / len(CIRCLE)
        cy = sum(pos[i][1] for i in CIRCLE) / len(CIRCLE)
        self.angle = {i: math.atan2(pos[i][1] - cy, pos[i][0] * accent.UPPER_ASPECT - cx * accent.UPPER_ASPECT)
                      for i in CIRCLE}
        bar = sorted((i for i in range(accent.UPPER) if self.role[i] == "marquee"), key=lambda i: pos[i][0])
        self.bulb = {i: k for k, i in enumerate(bar)}
        self.shimmer = [rnd.random() * 6.28 for _ in range(accent.NUM_PIXELS)]

    def frame(self, t):
        drift = (t / 150.0) % 1.0                        # palette drift
        out = [(0, 0, 0)] * accent.NUM_PIXELS

        def rgb(h, s, v):
            r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, max(0.0, min(1.0, v)))
            return (r, g, b)

        for tube in self.tubes:
            span = tube["hi"] - tube["lo"] or 1
            # Two bubbles per tube, rising bottom (y=hi) to top (y=lo).
            bubbles = [((t * tube["speed"] + tube["phase"] + k * 0.5) % 1.2) for k in (0, 1)]
            for i in tube["idx"]:
                rise = (tube["hi"] - self.pos[i][1]) / span    # 0 bottom .. 1 top
                glow = 0.28
                for b in bubbles:
                    glow += 0.75 * max(0.0, 1 - abs(rise - b) / 0.12)
                r, g, b_ = rgb(tube["hue"] + drift, 1.0 - 0.6 * min(1, glow - 0.28), glow)
                out[i] = (r, g, b_)
        step = int(t / 0.35)
        for i, k in self.bulb.items():
            on = (k + step) % 3 == 0
            out[i] = rgb(0.09, 0.75, 0.8 if on else 0.08)  # warm gold bulbs
        spin = t * 0.25 * 2 * math.pi
        for i, a in self.angle.items():
            d = (a - spin + math.pi) % (2 * math.pi) - math.pi
            v = 0.12 + 0.8 * max(0.0, 1 - abs(d) / 0.9)
            out[i] = rgb(0.85 + drift, 0.6, v)
        for i in range(accent.UPPER):
            if self.role[i] == "wave":
                x = self.pos[i][0]
                out[i] = rgb(0.55 + drift + 0.18 * math.sin(2 * math.pi * (x - 0.05 * t)), 0.9,
                             0.32 + 0.12 * math.sin(2 * math.pi * (x * 2 - 0.1 * t)))
        for i in range(accent.UPPER, accent.NUM_PIXELS):
            row = (self.pos[i][1] - accent.FOOT_Y0) / accent.FOOT_DY
            v = (0.42 - 0.08 * row) * (0.9 + 0.1 * math.sin(t * 1.3 + self.shimmer[i]))
            out[i] = rgb(0.07, 0.65, v)                  # warm amber wash
        return [(int(r * 255), int(g * 255), int(b * 255)) for r, g, b in out]


def limit(frame, scale):
    """Brightness, then the accent branch's current cap."""
    amps = sum(r + g + b for r, g, b in frame) / 255 * AMPS_PER_CHANNEL_FULL * scale
    if amps > accent.MAX_AMPS:
        scale *= accent.MAX_AMPS / amps
    return [(int(r * scale), int(g * scale), int(b * scale)) for r, g, b in frame]


def demo(seconds: float):
    from rpi_ws281x import Color, PixelStrip, ws
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    strip = PixelStrip(accent.NUM_PIXELS, accent.GPIO, 800000, 10, False, 255, 1, ws.WS2811_STRIP_GRB)
    strip.begin()
    diner = Diner(accent.positions())
    print(f"{len(diner.tubes)} bubble tubes, {len(diner.bulb)} marquee bulbs")
    start = time.time()
    try:
        while time.time() - start < seconds:
            for i, (r, g, b) in enumerate(limit(diner.frame(time.time() - start), BRIGHTNESS)):
                strip.setPixelColor(i, Color(r, g, b))
            strip.show()
            time.sleep(1 / FPS)
    finally:
        for i in range(accent.NUM_PIXELS):
            strip.setPixelColor(i, Color(0, 0, 0))
        strip.show()
        strip.show()


if __name__ == "__main__":
    demo(float(sys.argv[1]) if len(sys.argv) > 1 else 90)
