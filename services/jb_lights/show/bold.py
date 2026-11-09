"""Bold shows: strong, solid colour (the owner, Nov 9: "i want more solid
bold strong colors ... very strong, solid colors at 85+%").

Four looks, each drawn on every part of the cabinet from the same picture
(inches in the shared cabinet coordinates: the upper panel 0..22 x 0..9.2,
the level bar at y 19..21, the footlights at y 27..28), so each reads as
one thing across the whole machine:

  flames    fire licking up the machine: the footlights the bed of the
            fire, the level bar flames rising with the music, the upper
            panel tongues of flame climbing from its bottom edge, higher on
            the kick and in the big parts. Deep red to yellow, never white.
  strobe    flashing solid colours: the upper panel one solid colour on
            each step, black between; the level bar on the off-step (the
            two ping-pong); the footlights five blocks of colour. A calm
            part holds solid colours instead. Every area flashes at most
            MAX_HZ times a second (flash safety, like the director's
            PULSE_MIN_S).
  fever     Saturday Night Fever: a lighting rig at the top of the upper
            panel throwing solid beams down and across, sweeping, each with
            a soft haze at its edges; where they land, the footlights light
            solid pools of the beams' colours, black between, following the
            sweep; the level bar shows the pools up close, as gobo patterns
            (dots of two colours inside a rim).
  infinity  the infinity mirror (a 70s dance-floor tile, a mid-century
            jukebox's lower panel): rings of light going in and in, each a
            solid colour, falling toward the middle with the beat. The
            upper panel is one deep tunnel; the footlights and the level
            bar rows of infinity tiles, each its own colour, like a floor.

Colour: every palette colour is pushed to full saturation (vivid()), and
colours are mixed by hue (hue_mix()), never through grey -- mixing in RGB
is how blends between, say, pink and cyan go pastel. Nothing here mixes
toward white.
"""

import colorsys
import math

BLACK = (0.0, 0.0, 0.0)
TAU = 2 * math.pi
SAT = 0.9                    # the least saturation any colour here has
UPPER_W, UPPER_H = 22.0, 9.2
MAX_HZ = 3.0                 # an area flashes at most this often a second
STROBE_ON_S, STROBE_FADE_S = 0.07, 0.09
LUT_N = 48


def scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def vivid(c, sat=SAT):
    """The colour at full brightness and at least sat saturation (its hue kept)."""
    h, s, _ = colorsys.rgb_to_hsv(c[0] / 255, c[1] / 255, c[2] / 255)
    r, g, b = colorsys.hsv_to_rgb(h, max(s, sat), 1.0)
    return (r * 255, g * 255, b * 255)


def hue_mix(a, b, k):
    """a to b by hue, the short way round, at full saturation: never grey."""
    ha, sa, _ = colorsys.rgb_to_hsv(a[0] / 255, a[1] / 255, a[2] / 255)
    hb, sb, _ = colorsys.rgb_to_hsv(b[0] / 255, b[1] / 255, b[2] / 255)
    d = (hb - ha + 0.5) % 1.0 - 0.5
    r, g, bl = colorsys.hsv_to_rgb((ha + d * k) % 1.0, max(SAT, sa + (sb - sa) * k), 1.0)
    return (r * 255, g * 255, bl * 255)


_vivid, _luts = {}, {}


def colours(palette) -> tuple:
    """The palette, vivid (cached: it changes only between looks)."""
    key = tuple(tuple(c) for c in palette)
    got = _vivid.get(key)
    if got is None:
        if len(_vivid) > 256:
            _vivid.clear()
        got = _vivid[key] = tuple(vivid(c) for c in key)
    return got


def lut(palette) -> tuple:
    """The palette as a ramp (first colour .. last), by hue, LUT_N steps."""
    pal = colours(palette)
    got = _luts.get(pal)
    if got is None:
        if len(_luts) > 256:
            _luts.clear()
        n = len(pal)
        if n == 1:
            got = (pal[0],) * LUT_N
        else:
            out = []
            for i in range(LUT_N):
                x = i / (LUT_N - 1) * (n - 1)
                k = min(n - 2, int(x))
                out.append(hue_mix(pal[k], pal[k + 1], x - k))
            got = tuple(out)
        _luts[pal] = got
    return got


def steps_per_s(cues) -> float:
    return max(0.1, cues.rate * cues.tempo / 60)


def tier(cues) -> str:
    return cues.tier


def flash_env(cues, offset=0.0) -> tuple:
    """(which flash, its envelope 0..1): a flash on each movement step,
    halved until it's at most MAX_HZ a second; full for STROBE_ON_S, then
    gone in STROBE_FADE_S."""
    rate, div = steps_per_s(cues), 1
    while rate / div > MAX_HZ:
        div *= 2
    s = cues.move / div + offset
    k = math.floor(s)
    age = (s - k) * div / rate
    env = 1.0 if age < STROBE_ON_S else max(0.0, 1 - (age - STROBE_ON_S) / STROBE_FADE_S)
    return k, env


# Flames ----------------------------------------------------------------------------

def tongue(x, t, speed) -> float:
    """How high the flame reaches at x (inches), 0.1 .. 1."""
    return (0.55 + 0.2 * math.sin(0.85 * x + 2.1 * t * speed) + 0.15 * math.sin(2.3 * x - 3.3 * t * speed + 1.3)
            + 0.1 * math.sin(5.1 * x + 6.7 * t * speed + 0.4))


def flame_speed(cues) -> float:
    return 0.7 + 0.5 * min(2.0, steps_per_s(cues) / 2)


def fire_colour(ramp, heat):
    """heat 0..1: black, deep red, ... the hottest colour; tips fade to black."""
    if heat <= 0.0:
        return BLACK
    c = ramp[min(LUT_N - 1, int(heat * (LUT_N - 1)))]
    return scale(c, min(1.0, heat * 2.4))


# Saturday Night Fever --------------------------------------------------------------

SRC_Y = -0.8                 # the rig sits just above the upper panel
SPREAD = 0.8                 # radians either side of straight down
BEAM_W, HAZE_W = 0.075, 0.22  # the beam's core and its haze, radians either side
HAZE_V = 0.35                # the haze at the core's edge
FLOOR_D = 10.0               # how far below the rig the beams land (inches across per tan)


def rig(cues) -> list:
    """The beams: (source x, source y, angle from straight down, colour, which)."""
    pal = colours(cues.palette)
    tr = tier(cues)
    if tr == "calm":
        sources, n, period = (UPPER_W / 2,), 4, 24
    elif tr == "peak":
        sources, n, period = (UPPER_W * 0.3, UPPER_W * 0.7), 4, 8
    else:
        sources, n, period = (UPPER_W / 2,), 6, 16
    sweep = cues.move / period
    shift = int(cues.move // 8)                   # the colours move round every two bars
    beams = []
    for si, sx in enumerate(sources):
        for k in range(n):
            base = ((k - (n - 1) / 2) / ((n - 1) / 2)) * SPREAD * 0.75
            side = 1 if k % 2 else -1
            a = base + 0.35 * math.sin(TAU * sweep + k * 1.7 + si * 2.4) * side
            a = max(-1.2, min(1.2, a))
            beams.append((sx, SRC_Y, a, pal[(k + si * 2 + shift) % len(pal)], k + si * n))
    return beams


def beam_light(x, y, beams):
    """A point of the upper panel lit by the beams: (colour, 0..1)."""
    best, colour = 0.0, BLACK
    for sx, sy, a, c, _ in beams:
        d = abs(math.atan2(x - sx, y - sy) - a)
        if d < BEAM_W:
            v = 1.0
        elif d < HAZE_W:
            v = HAZE_V * (1 - (d - BEAM_W) / (HAZE_W - BEAM_W)) ** 2
        else:
            continue
        if v > best:
            best, colour = v, c
    return colour, best


def landings(beams) -> list:
    """Where each beam lands on the floor: (x inches, colour, which)."""
    return [(sx + FLOOR_D * math.tan(a), c, which) for sx, sy, a, c, which in beams]


# Infinity --------------------------------------------------------------------------

def ring_depth(d, r) -> float:
    """Rings at d = r**k: how many rings in from the edge d is (0 at the edge)."""
    return math.log(max(d, 1e-3)) / math.log(r)


def infinity_phase(cues) -> float:
    """How far the rings have fallen in: one ring a bar when calm, every
    other step usually, every step in the big parts."""
    per = {"calm": 0.25, "peak": 1.0}.get(tier(cues), 0.5)
    return cues.move * per


def ring_light(depth, phase, d, deep_floor=0.6, width=0.4):
    """(which ring, 0..1) at a depth: rings sharp, the deep ones dimmer."""
    z = depth - phase
    ring = round(z)
    e = abs(z - ring)
    if e >= width:
        return ring, 0.0
    return ring, (1 - e / width) * (deep_floor + (1 - deep_floor) * min(1.0, d) ** 0.6)


# The parts -------------------------------------------------------------------------

class UpperBold:
    """The upper panel's four looks. xy: its pixels in inches."""

    def __init__(self, xy):
        self.xy = xy
        cx, cy = UPPER_W / 2, UPPER_H / 2
        self.inf = []
        for x, y in xy:
            nx, ny = (x - cx) / cx, (y - cy) / cy
            d = max(abs(nx), abs(ny))
            self.inf.append((d, ring_depth(d, 0.72)))

    def flames(self, cues, snap, t, dt):
        ramp = lut(cues.palette)
        speed = flame_speed(cues)
        boost = {"calm": 0.75, "peak": 1.3}.get(tier(cues), 1.0)
        reach = (0.3 + 0.35 * cues.level + 0.35 * cues.pulse["drive"]) * boost
        out = []
        for x, y in self.xy:
            u = 1 - y / UPPER_H                                  # 0 the bottom edge .. 1 the top
            wx = x + 0.7 * math.sin(3.0 * u - t * 4.0 * speed)   # the flame wavers as it climbs
            h = tongue(wx, t, speed) * reach * 1.3
            out.append(fire_colour(ramp, 1 - u / h if h > 0 and u < h else 0.0))
        return out

    def strobe(self, cues, snap, t, dt):
        pal = colours(cues.palette)
        n = len(pal)
        tr = tier(cues)
        if tr == "calm":
            c = scale(pal[int(cues.move // 4) % n], 0.85 + 0.15 * cues.pulse["drive"])
            return [c] * len(self.xy)
        if tr == "peak":                                      # the halves take turns, two colours
            kl, el = flash_env(cues)
            kr, er = flash_env(cues, 0.5)
            left, right = scale(pal[kl % n], el), scale(pal[(kr + 1) % n], er)
            return [left if x < UPPER_W / 2 else right for x, _ in self.xy]
        k, env = flash_env(cues)
        return [scale(pal[k % n], env)] * len(self.xy)

    def fever(self, cues, snap, t, dt):
        beams = rig(cues)
        hit = 0.85 + 0.15 * cues.pulse["drive"]
        out = []
        for x, y in self.xy:
            near = min(math.hypot(x - sx, y - sy) for sx, sy, *_ in beams)
            colour, v = beam_light(x, y, beams)
            if near < 1.8:                                    # the rig itself, blazing
                colour = colour if v > 0 else beams[0][3]
                v = 1.0
            out.append(scale(colour, v * hit * (1 - 0.12 * y / UPPER_H)))
        return out

    def infinity(self, cues, snap, t, dt):
        pal = colours(cues.palette)
        phase = infinity_phase(cues)
        hit = 0.85 + 0.15 * cues.pulse["drive"]
        out = []
        for d, depth in self.inf:
            if d < 0.07:
                out.append(BLACK)                              # the far end of the tunnel
                continue
            ring, v = ring_light(depth, phase, d)
            out.append(scale(pal[ring % len(pal)], v * hit) if v else BLACK)
        return out


class GridBold:
    """The footlights' and the level bar's looks: a grid of pixels.

    cells: (index into the part's frame, column, row, x inches, y inches),
    rows counted from the back/top (0) to the front/bottom (rows - 1)."""

    def __init__(self, cells, cols, rows, tile_cols):
        self.cells, self.cols, self.rows = cells, cols, rows
        self.size = len(cells)
        self.tile_cols = tile_cols
        self.x0 = min(c[3] for c in cells)
        self.x1 = max(c[3] for c in cells)
        tiles = cols // tile_cols
        self.tiles = []                                         # per cell: (tile, d, depth)
        for _, col, row, _, _ in cells:
            j = min(tiles - 1, col // tile_cols)
            tx = (col - j * tile_cols - (tile_cols - 1) / 2) / (tile_cols / 2)
            ty = (row - (rows - 1) / 2) / (rows / 2)
            d = max(abs(tx), abs(ty))
            self.tiles.append((j, d, ring_depth(d, 0.6)))

    def blank(self):
        return [BLACK] * self.size

    def _bed(self, cues, t, low_front=True):
        """The fire's bed: every pixel burning, the front hottest, flaring on the drive."""
        ramp = lut(cues.palette)
        speed = flame_speed(cues)
        out = self.blank()
        drive = cues.pulse["drive"]
        v = 0.5 + 0.5 * drive                                   # the fire flares on the beat
        for i, col, row, x, y in self.cells:
            front = row / (self.rows - 1) if low_front else 1 - row / (self.rows - 1)
            heat = (0.3 + 0.5 * tongue(x * 1.4, t * 1.5, speed) + 0.2 * drive) * (0.65 + 0.35 * front)
            out[i] = scale(ramp[min(LUT_N - 1, int(heat * (LUT_N - 1)))], v)
        return out

    def flames_foot(self, cues, snap, t, dt):
        return self._bed(cues, t)

    def flames_bar(self, cues, snap, t, dt):
        """Flames rising off the bar's bottom row with the music."""
        ramp = lut(cues.palette)
        speed = flame_speed(cues)
        bands = snap.resample(self.cols) if snap is not None else [0.5] * self.cols
        boost = {"calm": 0.75, "peak": 1.25}.get(tier(cues), 1.0)
        out = self.blank()
        for i, col, row, x, y in self.cells:
            u = (self.rows - 1 - row + 0.5) / self.rows             # 0 the bottom .. 1 the top
            h = (0.25 + 0.6 * min(1.0, bands[col] * (0.5 + 0.6 * cues.level)) + 0.25 * cues.pulse["drive"])
            h *= (0.6 + 0.6 * tongue(x * 3, t, speed)) * boost
            out[i] = fire_colour(ramp, 1 - u / h if h > 0 and u < h else 0.0)
        return out

    def strobe(self, cues, snap, t, dt, offset=0.0, blocks=5):
        pal = colours(cues.palette)
        n = len(pal)
        tr = tier(cues)
        out = self.blank()
        width = self.cols / blocks
        if tr == "calm":
            shift = int(cues.move // 4)
            v = 0.85 + 0.15 * cues.pulse["drive"]
            for i, col, *_ in self.cells:
                out[i] = scale(pal[(int(col // width) + shift) % n], v)
            return out
        k, env = flash_env(cues, offset)
        if tr == "peak":                                      # odd and even blocks take turns
            k2, env2 = flash_env(cues, offset + 0.5)
        for i, col, *_ in self.cells:
            b = int(col // width)
            if tr == "peak" and b % 2:
                out[i] = scale(pal[(k2 + b) % n], env2)
            else:
                out[i] = scale(pal[(k + b) % n], env)
        return out

    def fever_foot(self, cues, snap, t, dt):
        """Pools of the beams' colours on the floor, black between, sweeping with them."""
        hit = 0.85 + 0.15 * cues.pulse["drive"]
        spots = landings(rig(cues))
        out = self.blank()
        for i, col, row, x, y in self.cells:
            for lx, c, which in spots:
                rc = (which % 3) * (self.rows - 1) / 2                # pools at the back, middle and front
                dx, dy = (x - lx) / 1.5, (row - rc) / 1.8
                if dx * dx + dy * dy < 1:
                    out[i] = scale(c, hit)
                    break
        return out

    def fever_bar(self, cues, snap, t, dt):
        """The pools up close: gobo dots of two colours inside a rim of a third."""
        pal = colours(cues.palette)
        n = len(pal)
        hit = 0.85 + 0.15 * cues.pulse["drive"]
        spots = landings(rig(cues))
        span = self.x1 - self.x0 or 1
        flip = int(cues.move) % 2                                 # the gobo turns on each step
        out = self.blank()
        for i, col, row, x, y in self.cells:
            fx = (x - self.x0) / span * UPPER_W                    # the bar shows the whole floor
            for lx, c, which in spots:
                dx = (fx - lx) / 2.4
                dy = (row - (1.5 if which % 2 else 2.5)) / 2.6
                r = dx * dx + dy * dy
                if r < 1:
                    k = pal.index(c) if c in pal else 0
                    if r > 0.55:
                        colour = pal[(k + 2) % n]                    # the rim
                    else:
                        colour = c if (col + row + flip) % 2 else pal[(k + 1) % n]
                    out[i] = scale(colour, hit)
                    break
        return out

    def infinity(self, cues, snap, t, dt):
        """A row of infinity tiles, each its own colour, the rings falling in."""
        pal = colours(cues.palette)
        n = len(pal)
        phase = infinity_phase(cues)
        step = int(cues.move // 2)                              # the floor changes colour every other step
        hit = 0.85 + 0.15 * cues.pulse["drive"]
        out = self.blank()
        for (i, *_), (j, d, depth) in zip(self.cells, self.tiles):
            _, v = ring_light(depth, phase, d, deep_floor=0.65, width=0.4)
            if v:
                out[i] = scale(pal[(j * 2 + step) % n], v * hit)
        return out


def keys_solid(cues, row_x, dt):
    """The keypad in solid palette colours, a few keys each, pulsed by the drive."""
    pal = colours(cues.palette)
    n = len(pal)
    shift = int(cues.move // 4)
    v = 0.6 + 0.4 * cues.pulse["drive"]
    return {i: scale(pal[(int(x * n * 0.999) + shift) % n], v) for i, x in row_x.items()}
