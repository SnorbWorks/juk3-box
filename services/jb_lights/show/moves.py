"""The song-aware show's own moves, for every part of the cabinet (parts.py
calls them with the part's pixels in inches, in the shared cabinet picture):

  starfield  stars flying out from the middle, faster as they go (a 70s or
             80s song, an alternative one): their speed is the plan's pace,
             the kick pushes them on
  flow       slow waves of the palette rolling across (a love song, a ballad):
             the swell follows how loud the song has been lately, not each hit
  beams      a rock show's moving heads: beams sweeping across in pairs on
             the plan's pace, hit by the kick, more of them in a big section
  splash     a crash cymbal's light: a ring spreading from where it struck,
             across every part (a shimmer: a soft glow, for a ballad)

Pure but for each move's own random stars. Cheap enough for 40 frames a
second on a Pi 4: stars are found through a grid of the pixels, not by
looking at every pixel for every star.
"""

import math
import random

BLACK = (0.0, 0.0, 0.0)
WHITE = (255.0, 255.0, 255.0)
TAU = 2 * math.pi
STAR_CELL_IN = 1.5           # the grid stars find their pixels by
STAR_SIZE_IN = (0.5, 1.6)    # a star's size, near the middle .. at the edge
SPLASH_IN_S = 30.0           # how fast a splash's ring spreads, inches a second
SHIMMER_IN = (6.0, 14.0)     # a shimmer's glow: its size, and how far it grows a second


def scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def lerp(a, b, k):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def along(palette, x):
    n = len(palette)
    x = (x % 1.0) * n
    k = int(x)
    return lerp(palette[k % n], palette[(k + 1) % n], x - k)


def bounds(xy):
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return min(xs), min(ys), max(xs), max(ys)


def normalised(xy):
    """Each pixel's place within its part, 0..1 both ways."""
    x0, y0, x1, y1 = bounds(xy)
    return [((x - x0) / (x1 - x0 or 1), (y - y0) / (y1 - y0 or 1)) for x, y in xy]


class Starfield:
    """Stars flying out from the middle of a part."""

    def __init__(self, xy_in, seed=1, centre=None):
        self.xy = xy_in
        x0, y0, x1, y1 = bounds(xy_in)
        self.centre = centre or ((x0 + x1) / 2, (y0 + y1) / 2)
        cx, cy = self.centre
        self.reach = max(math.hypot(x - cx, y - cy) for x, y in xy_in) + 1.0
        self.grid = {}
        for i, (x, y) in enumerate(xy_in):
            self.grid.setdefault((int(x // STAR_CELL_IN), int(y // STAR_CELL_IN)), []).append(i)
        self.rnd = random.Random(seed)
        self.stars = []              # [angle, distance (in), colour k]
        self.n = len(xy_in)

    def frame(self, cues, dt):
        pace = cues.rate * cues.tempo / 60                       # movement steps a second
        speed = (5 + 9 * pace) * (0.6 + 0.8 * cues.level) * (1 + 1.2 * cues.pulse["kick"] * (1 - cues.smooth))
        want = int(14 + 26 * cues.level)
        while len(self.stars) < want:
            self.stars.append([self.rnd.uniform(0, TAU), self.rnd.uniform(0, 0.2) * self.reach,
                               self.rnd.randrange(len(cues.palette))])
        cx, cy = self.centre
        out = [BLACK] * self.n
        keep = []
        lo, hi = STAR_SIZE_IN
        for star in self.stars:
            a, r, k = star
            r += speed * dt * (0.2 + r / self.reach)               # nearer the edge, faster: it's coming at you
            star[1] = r
            if r > self.reach:
                continue
            keep.append(star)
            near = r / self.reach
            bright = min(1.0, near * 3) * (0.5 + 0.5 * near)
            size = lo + (hi - lo) * near
            sx, sy = cx + r * math.cos(a), cy + r * math.sin(a)
            colour = lerp(cues.palette[k % len(cues.palette)], WHITE, 0.5 * near)
            gx, gy = int(sx // STAR_CELL_IN), int(sy // STAR_CELL_IN)
            for ix in (gx - 1, gx, gx + 1):
                for iy in (gy - 1, gy, gy + 1):
                    for i in self.grid.get((ix, iy), ()):
                        px, py = self.xy[i]
                        d = math.hypot(px - sx, py - sy)
                        if d < size:
                            v = bright * (1 - d / size)
                            o = out[i]
                            if v * 255 > max(o):
                                out[i] = scale(colour, v)
        self.stars = keep
        return out


def flow(xy_norm, cues, t):
    """Slow waves of the palette rolling across; the swell follows the song lately."""
    phase = cues.move * 0.12 + t * 0.015
    swell = 0.3 + 0.5 * cues.loud + 0.2 * cues.level
    pal = cues.palette
    out = []
    for x, y in xy_norm:
        u = x * 0.9 + 0.35 * math.sin(TAU * (y * 0.6 + phase * 0.5)) + phase
        v = 0.55 + 0.45 * math.sin(TAU * (x * 1.3 - y * 0.7 + phase * 0.8))
        out.append(scale(along(pal, u), swell * (0.35 + 0.65 * v)))
    return out


def beams(xy_norm, cues, count=None):
    """A rock show's beams from the top of a part, sweeping in pairs, hit by the kick."""
    n = count or (5 if cues.character in ("big", "solo") else 3)
    sweep = cues.move / 8
    hit = 0.3 + 0.7 * cues.pulse["drive"]
    pal = cues.palette
    beams_ = []
    for k in range(n):
        side = 1 if k % 2 else -1
        top = (k + 0.5) / n
        tilt = 0.45 * math.sin(TAU * sweep + k * 2.1) * side
        beams_.append((top, tilt, pal[k % len(pal)]))
    out = []
    haze = scale(pal[0], 0.04)
    for x, y in xy_norm:
        best, colour = 0.0, None
        w = 0.05 + 0.04 * y
        for top, tilt, c in beams_:
            d = (x - (top + tilt * y)) / w
            if -1 < d < 1:
                v = 1 - d * d
                if v > best:
                    best, colour = v, c
        if colour is None:
            out.append(haze)
            continue
        c = lerp(colour, WHITE, 0.5 * max(0.0, 1 - y / 0.2))        # white where they leave the rig
        out.append(scale(c, max(0.04, best * hit * (1 - 0.35 * y))))
    return out


def splash(frame, cues, xy_in):
    """The crash cymbals' splashes, over a part's frame (every part draws the
    same splashes, so each spreads across the whole cabinet from one point)."""
    if not cues.splashes:
        return frame
    out = list(frame)
    for age, sx, sy, v, colour, kind in cues.splashes:
        if kind == "shimmer":
            size = SHIMMER_IN[0] + SHIMMER_IN[1] * age
            fade = v * 0.35 * max(0.0, 1 - age / 1.8)
            reach, ring, w = size, None, None
        else:
            ring, w = SPLASH_IN_S * age, 4.0 + 6.0 * age
            fade = v * max(0.0, 1 - age / 0.9) ** 1.5
            reach = ring + w
        if fade <= 0.01:
            continue
        for i, (px, py) in enumerate(xy_in):
            dx, dy = px - sx, py - sy
            if abs(dx) > reach or abs(dy) > reach:
                continue
            d = math.hypot(dx, dy)
            if ring is None:
                k = fade * (1 - d / reach) if d < reach else 0.0
            else:
                k = fade * max(max(0.0, 1 - abs(d - ring) / w), 0.3 if d < ring else 0.0)
            if k > 0.01:
                out[i] = lerp(out[i], colour, min(1.0, k))
    return out
