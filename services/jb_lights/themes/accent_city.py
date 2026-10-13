"""accent-city: the idle look for the accent chain -- a city at night.

The user's design (2026-10-12): the panel's artwork reads as an abstract
skyline, so at idle it should look like a city at night, in slow motion.

  - logo bar (the 24-px top bar behind the Rock-Ola logo) -> steady white
  - columns = skyscrapers -> dark buildings with lit windows, warm and
    uneven, switching on and off now and then; red aviation lights blinking
    on top of the two tallest
  - horizontal bars = streets -> dim amber street light, slow-motion cars:
    white headlights one way, red taillights the other
  - the two swooshes = bridges -> a string of soft bridge lights, the odd
    car crossing
  - circle -> the moon, a soft pale glow breathing very slowly
  - footlights -> the city reflected on the grill below: a night-blue glow
    with the warm light of the windows above each spot, and every car
    traced along them as it drives by (user, 2026-10-13)

Shapes come from the same map-based detection as accent-diner; bridges are
pixels 92-104 and the circle 77-85 (their chain ranges from the mapping).

    sudo venv/bin/python -m services.jb_lights.themes.accent_city [seconds]
"""

import math
import random
import signal
import sys
import time

from services.jb_lights import accent
from services.jb_lights.themes.accent_diner import CIRCLE, Diner, limit, segments

FPS = 40
BRIGHTNESS = 0.6
BRIDGES = range(91, 104)          # chain indices of the swooshes (pixels 92-104)

LOGO = (255, 245, 230)            # white behind the logo
BUILDING = (6, 6, 16)             # unlit building, a whisper of blue
WINDOW = (255, 175, 80)           # warm window light
AVIATION = (255, 0, 0)
STREET = (40, 22, 4)              # dim sodium-amber street light
HEADLIGHT = (255, 240, 210)
TAILLIGHT = (255, 10, 0)
BRIDGE = (70, 80, 110)            # cool bridge-cable lights
MOON = (150, 170, 210)
NIGHT = (10, 22, 80)             # footlights: the night sky's blue between the buildings
FOOT_WINDOW = (150, 100, 40)      # footlights: window light spilling down

WINDOW_LIT = 0.38                 # share of windows lit at any moment
WINDOW_TOGGLE_PER_S = 0.012       # chance per window per second to switch
CAR_SPEED = (1.2, 2.2)            # pixels per second: slow motion
CAR_SPAWN_PER_S = 0.06            # per street, per second
BLINK_S, BLINK_ON_S = 1.8, 0.35   # aviation lights: a short flash every 1.8 s
FOOT_SPREAD = 0.04                # footlights reflect the windows within about this much x
FOOT_WARM = (0.25, 0.5)           # lit share of the windows above: blue below, fully warm above
FOOT_CAR = 0.7                    # a car's trace on the footlights


def mix(c, k):
    return tuple(v * k for v in c)


def add(a, b):
    return tuple(min(255.0, x + y) for x, y in zip(a, b))


def blend(a, b, k):
    return tuple(x + (y - x) * k for x, y in zip(a, b))


class City:
    def __init__(self, pos, seed=11):
        self.rnd = random.Random(seed)
        shapes = Diner(pos)
        self.pos = pos
        self.logo = list(shapes.bulb)
        self.moon = list(CIRCLE)
        self.buildings = [t["idx"] for t in shapes.tubes]              # each bottom -> top
        tallest = sorted(self.buildings, key=lambda b: pos[b[-1]][1])[:2]
        self.beacons = [(b[-1], phase) for b, phase in zip(tallest, (0.0, 0.9))]
        beacon_px = {i for i, _ in self.beacons}
        self.windows = {i: self.rnd.random() < WINDOW_LIT for b in self.buildings for i in b if i not in beacon_px}
        self.window_level = {i: self.rnd.uniform(0.35, 1.0) for i in self.windows}
        taken = set(self.logo) | set(self.moon) | {i for b in self.buildings for i in b}
        self.streets, self.bridges = [], []
        for seg in segments(pos):
            seg = [i for i in seg if i not in taken]
            if len(seg) < 2:
                continue
            if any(i in BRIDGES for i in seg):
                self.bridges.append(seg)                                 # chain order follows the curve
            else:
                self.streets.append(sorted(seg, key=lambda i: pos[i][0]))  # left -> right
        # Footlights: which windows each column reflects.
        self.reflect = []
        for col in range(accent.FOOT_COLS):
            x = (col + 0.5) / accent.FOOT_COLS
            w = [(i, math.exp(-((pos[i][0] - x) / FOOT_SPREAD) ** 2)) for i in self.windows]
            w = [(i, k) for i, k in w if k > 0.02]
            total = sum(k for _, k in w)
            self.reflect.append([(i, k / total) for i, k in w])
        self.cars = []          # [lane, position (px along lane), speed (px/s, signed), colour]
        for lane in self.streets + self.bridges:
            if self.rnd.random() < 0.6:
                self._spawn(lane, anywhere=True)

    def _spawn(self, lane, anywhere=False):
        speed = self.rnd.uniform(*CAR_SPEED)
        rightward = self.rnd.random() < 0.5
        n = len(lane)
        start = self.rnd.uniform(0, n - 1) if anywhere else (-1.0 if rightward else n)
        self.cars.append([lane, start, speed if rightward else -speed,
                          HEADLIGHT if rightward else TAILLIGHT])

    def frame(self, t, dt):
        out = [(0.0, 0.0, 0.0)] * accent.NUM_PIXELS
        for i in self.logo:
            out[i] = LOGO
        moon = 0.75 + 0.25 * math.sin(t * 2 * math.pi / 14)            # very slow breath
        for i in self.moon:
            out[i] = mix(MOON, moon)

        # Windows switch on and off now and then.
        for i, lit in self.windows.items():
            if self.rnd.random() < WINDOW_TOGGLE_PER_S * dt:
                self.windows[i] = not lit
                if not lit:
                    self.window_level[i] = self.rnd.uniform(0.35, 1.0)
            out[i] = mix(WINDOW, 0.55 * self.window_level[i]) if self.windows[i] else BUILDING
        for i, phase in self.beacons:
            on = ((t + phase) % BLINK_S) < BLINK_ON_S
            out[i] = AVIATION if on else BUILDING

        for lane in self.streets:
            for i in lane:
                out[i] = STREET
        for lane in self.bridges:
            for k, i in enumerate(lane):
                twinkle = 0.75 + 0.25 * math.sin(t * 0.7 + k * 1.3)
                out[i] = mix(BRIDGE, twinkle)

        # Cars: a soft two-pixel glow sliding along their lane.
        alive = []
        for car in self.cars:
            lane, p, v, colour = car
            p += v * dt
            car[1] = p
            if -2 <= p <= len(lane) + 1:
                alive.append(car)
            for k, i in enumerate(lane):
                w = max(0.0, 1 - abs(k - p) / 1.3)
                if w:
                    out[i] = add(out[i], mix(colour, 0.85 * w))
        self.cars = alive
        for lane in self.streets + self.bridges:
            if self.rnd.random() < CAR_SPAWN_PER_S * dt and sum(c[0] is lane for c in self.cars) < 2:
                self._spawn(lane)

        # Footlights: night blue, breathing, turning warm under lit windows;
        # each car leaves a trace at its x as it drives by.
        night = mix(NIGHT, 0.8 + 0.2 * math.sin(t * 2 * math.pi / 9))
        foot = []
        for weights in self.reflect:
            lit = sum(k * self.window_level[i] for i, k in weights if self.windows[i])
            foot.append(blend(night, FOOT_WINDOW, min(1.0, max(0.0, (lit - FOOT_WARM[0]) / (FOOT_WARM[1] - FOOT_WARM[0])))))
        for lane, p, _, colour in self.cars:
            k = min(len(lane) - 1.0, max(0.0, p))
            a, z = lane[int(k)], lane[min(len(lane) - 1, int(k) + 1)]
            x = self.pos[a][0] + (self.pos[z][0] - self.pos[a][0]) * (k - int(k))
            edge = min(1.0, p + 1.5, len(lane) + 0.5 - p)               # fade in/out at the lane's ends
            for col in range(accent.FOOT_COLS):
                w = max(0.0, 1 - abs((col + 0.5) / accent.FOOT_COLS - x) * accent.FOOT_COLS / 1.6)
                if w and edge > 0:
                    foot[col] = add(foot[col], mix(colour, FOOT_CAR * w * edge))
        for row in range(accent.FOOT_ROWS):
            for col in range(accent.FOOT_COLS):
                out[accent.foot_index(row, col)] = foot[col]
        return [(int(r), int(g), int(b)) for r, g, b in out]


def demo(seconds: float):
    from rpi_ws281x import Color, PixelStrip, ws
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    strip = PixelStrip(accent.NUM_PIXELS, accent.GPIO, 800000, 10, False, 255, 1, ws.WS2811_STRIP_GRB)
    strip.begin()
    city = City(accent.positions())
    print(f"{len(city.buildings)} buildings, beacons on pixels {[i + 1 for i, _ in city.beacons]}, "
          f"{len(city.streets)} streets, {len(city.bridges)} bridges")
    start = last = time.time()
    try:
        while time.time() - start < seconds:
            now = time.time()
            frame = city.frame(now - start, now - last)
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
    demo(float(sys.argv[1]) if len(sys.argv) > 1 else 120)
