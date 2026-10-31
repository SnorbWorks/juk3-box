"""The show's parts: each section of the cabinet drawing its own role from
the director's cues (director.py), in the scenes the style names
(styles.py).

  Upper  (accent chain 0-188) the storyteller
           breathe  the whole panel one slow breath of the palette
           wash     the palette drifting across, swelling with the music
           chase    the columns lit one after another, a step per beat
           sweep    a bright band sweeping across on each bar's downbeat
           rise     each column a slice of the spectrum, filling bottom up
           slab     one big block of colour, a new one every bar, pumped by the drive
           wobble   dubstep: lit from the centre out as wide as the bass is loud
           marquee  the old jukebox: chasing bulbs, bubble tubes, a spinning record
           tubes    bubble tubes filling with the music
           eq       the classic EQ look (themes/accent_eq.py)
           ribbon   the flowing ribbon (themes/accent_ribbon.py)
           arena    the whole cabinet dark with blue sparkles, bursting white (show/arena.py), in every part
  Foot   (accent chain 189-348, 4 rows x 40) the rhythm section
           glow / pulse / rows (kick front, snare back) / chase / checker / offbeat
  Bar    (the 36 x 5 level bar) the secondary voice, as chunky blocks or lines:
           spectrum / classic / vu / ribbon, the music itself
           drumkit  a kit seen from above: hi-hat, snare, kick, tom, crash, each hit by its own drum
           tiles    the spectrum as six fat glowing tiles
           mirror   the spectrum folded: bass in the middle, highs at both ends
           bounce   a ball landing on every beat, crossing once a bar
           trace    the last 3 seconds scrolling by like a heart monitor
           ripple   each kick sending a ring out from the middle
           counter  four blocks counting the bar
           comet    a streak across on every beat
           fire     flames rising with the music
           rain     a raindrop for each hi-hat
           sparks   hits bursting into sparks, hi-hats throwing single ones
  Keys   (the keypad) the sparkle: sparkle / eq / pulse / glow / arena

Every part renders 0..255 RGB floats; finish() applies the director's
brightness, blackout and flash. Scene changes crossfade.
"""

import math
import random

from services.jb_lights import accent, buttons, matrix
from services.jb_lights.show import arena
from services.jb_lights.themes import volume_bar_theme2 as bar_ribbon, volume_bar_theme3 as bar_spectrum
from services.jb_lights.themes.accent_diner import CIRCLE, Diner
from services.jb_lights.themes.accent_eq import EQ
from services.jb_lights.themes.accent_ribbon import AccentRibbon

WHITE = (255.0, 255.0, 255.0)
BLACK = (0.0, 0.0, 0.0)
CROSSFADE_S = 0.8
TIER_GAIN = {"calm": 0.45, "main": 0.8, "peak": 1.0}   # where in the style's light range each tier sits

# Where each letter and digit sits on the keypad, left to right (0..1); the
# white-filtered keys pulse white.
ROW_X = {buttons.index(key): k / (len(row) - 1) for row in (buttons.LETTERS, buttons.DIGITS) for k, key in enumerate(row)}
WHITE_KEYS = (buttons.SINGLE, buttons.ALBUM, buttons.RESET)


def scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def lerp(a, b, k):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def along(palette, x):
    """The palette as a smooth loop: x 0..1 runs through every colour and back to the first."""
    n = len(palette)
    x = (x % 1.0) * n
    k = int(x)
    return lerp(palette[k % n], palette[(k + 1) % n], x - k)


def fresh_hat(owner, cues):
    """True on the frame a new hi-hat lands (its pulse jumps up)."""
    hat = cues.pulse["hat"]
    new = hat > 0.55 and hat > getattr(owner, "_hat", 0.0) + 0.15
    owner._hat = hat
    return new


def steps(cues, per_beat=1.0):
    """Beats as movement steps: half time, double time, faster as a build rises."""
    pace = {"half": 0.5, "beat": 1.0, "double": 2.0}[cues.style.pace]
    if cues.tempo > 140 and pace >= 1.0:
        pace /= 2                                  # fast songs move at half time
    elif cues.tempo < 80:
        pace *= 2
    speed = 4 if cues.tension > 0.85 else 2 if cues.tension > 0.5 else 1
    return cues.beat * pace * per_beat * speed


def gain(cues, part):
    lo, hi = cues.style.light
    g = lo + (hi - lo) * TIER_GAIN[cues.tier]
    if part == "upper":
        g *= 1 - 0.45 * cues.tension               # hold back as the build rises
    return g * (1 - 0.4 * cues.cool)


def finish(frame, cues, part):
    """Brightness, then the blackout, then the flash."""
    g = gain(cues, part) * (1 - cues.black)
    f = cues.flash if part != "keys" else cues.flash * 0.8
    out = []
    for c in frame:
        c = (c[0] * g, c[1] * g, c[2] * g)
        out.append(lerp(c, WHITE, f) if f > 0.01 else c)
    return out


class Scenes:
    """A part's scenes, crossfading when the director changes them."""

    def __init__(self, size):
        self.size = size
        self.current = self.previous = None
        self.fade = 1.0

    def frame(self, name, cues, snap, t, dt):
        if name != self.current:
            self.previous, self.current, self.fade = self.current, name, 0.0 if self.current else 1.0
        self.fade = min(1.0, self.fade + dt / CROSSFADE_S)
        out = getattr(self, name)(cues, snap, t, dt)
        if self.fade < 1.0 and self.previous:
            before = getattr(self, self.previous)(cues, snap, t, 0.0)
            out = [lerp(a, b, self.fade) for a, b in zip(before, out)]
        return out


class Upper(Scenes):
    def __init__(self, pos):
        super().__init__(accent.UPPER)
        shapes = Diner(pos)
        self.pos = pos[:accent.UPPER]
        ys = [p[1] for p in self.pos]
        self.y0, self.y1 = min(ys), max(ys)
        self.columns = sorted((t["idx"] for t in shapes.tubes), key=lambda c: pos[c[0]][0])
        self.column_of = {i: k for k, c in enumerate(self.columns) for i in c}
        self.height = {i: k / max(1, len(c) - 1) for c in self.columns for k, i in enumerate(c)}  # 0 bottom .. 1 top
        self.logo = sorted(shapes.bulb, key=lambda i: pos[i][0])
        self.circle = list(CIRCLE)
        self.angle = shapes.angle
        self.x = [p[0] for p in self.pos]
        self.eq_look, self.ribbon_look = EQ(pos), AccentRibbon(pos)
        self.ribbon_clock = 0.0
        self.slab_bar = None
        self.slab_colour = 0
        self.peaks = [0.0] * len(self.columns)
        self.arena_look = arena.UpperArena(pos)

    def _record(self, out, cues, colour):
        """The circle as a spinning record: one highlight going round once a bar."""
        spin = (cues.beat / 4) * 2 * math.pi
        for i in self.circle:
            d = (self.angle[i] - spin + math.pi) % (2 * math.pi) - math.pi
            out[i] = scale(colour, 0.15 + 0.85 * max(0.0, 1 - abs(d) / 0.9))

    def breathe(self, cues, snap, t, dt):
        period = 8 * 60 / cues.tempo if cues.style.beat_locked else 7.0
        v = 0.45 + 0.3 * math.sin(2 * math.pi * t / period) + 0.25 * cues.level
        drift = t / 40
        return [scale(along(cues.palette, x * 0.6 + drift), v) for x in self.x]

    def wash(self, cues, snap, t, dt):
        drift = cues.beat / 64 if cues.style.beat_locked else t / 30
        v = 0.4 + 0.6 * cues.level
        out = [scale(along(cues.palette, x * 0.8 + drift), v) for x in self.x]
        self._record(out, cues, cues.palette[-1])
        return out

    def chase(self, cues, snap, t, dt):
        n = len(self.columns)
        pos = steps(cues) % n
        out = []
        for i, x in enumerate(self.x):
            k = self.column_of.get(i)
            colour = along(cues.palette, x)
            if k is None:
                out.append(scale(colour, 0.25 + 0.3 * cues.pulse["drive"]))
                continue
            behind = (pos - k) % n                  # 0 = lit now, then the tail
            out.append(scale(colour, 0.12 + 0.88 * max(0.0, 1 - behind / 2.5)))
        bulbs = len(self.logo)
        lit = int(steps(cues, 2)) % 3
        for k, i in enumerate(self.logo):
            out[i] = scale(cues.palette[0], 1.0 if k % 3 == lit else 0.2)
        self._record(out, cues, cues.palette[-1])
        return out

    def sweep(self, cues, snap, t, dt):
        bar = cues.beat / 4
        p = (bar % 1.0) * 4                         # 0..1 through the bar's first beat
        backwards = int(bar) % 2
        centre = (1.2 * p - 0.1) if p < 1 else None
        if centre is not None and backwards:
            centre = 1 - centre
        out = []
        for x in self.x:
            c = along(cues.palette, x * 0.5 + bar / 8)
            v = 0.25 + 0.2 * cues.level
            if centre is not None:
                v += 0.75 * max(0.0, 1 - abs(x - centre) / 0.12)
            out.append(scale(c, min(1.0, v)))
        self._record(out, cues, cues.palette[0])
        return out

    def rise(self, cues, snap, t, dt):
        n = len(self.columns)
        bands = snap.resample(n) if n else []
        gain_ = 0.6 + 0.6 * cues.level
        for k in range(n):
            v = min(1.0, bands[k] * gain_)
            self.peaks[k] = max(v, self.peaks[k] - dt * 0.6)
        out = [scale(along(cues.palette, x), 0.12) for x in self.x]
        for k, col in enumerate(self.columns):
            fill = min(1.0, bands[k] * gain_)
            for i in col:
                h = self.height[i]
                colour = lerp(cues.palette[0], cues.palette[-1], h)
                if h <= fill:
                    out[i] = colour
                elif abs(h - self.peaks[k]) < 0.12:
                    out[i] = scale(WHITE, 0.7)
        self._record(out, cues, cues.palette[-1])
        return out

    def slab(self, cues, snap, t, dt):
        bar = int(steps(cues) // 4)
        if bar != self.slab_bar:
            self.slab_bar = bar
            self.slab_colour = (self.slab_colour + 1) % len(cues.palette)
        colour = cues.palette[self.slab_colour % len(cues.palette)]
        v = 0.45 + 0.55 * cues.pulse["drive"]
        return [scale(colour, v)] * self.size

    def wobble(self, cues, snap, t, dt):
        bass = sum(snap.bands[:6]) / 6 if snap.bands else 0.0
        width = 0.08 + 0.5 * min(1.0, bass * (0.6 + 0.6 * cues.level))
        out = []
        for i, x in enumerate(self.x):
            d = abs(x - 0.5)
            c = along(cues.palette, d * 2)
            out.append(scale(c, 1.0 if d < width else 0.04))
        return out

    def marquee(self, cues, snap, t, dt):
        warm = cues.palette[0]
        out = []
        for i, x in enumerate(self.x):
            k = self.column_of.get(i)
            if k is None:
                out.append(scale(along(cues.palette, x * 0.5 + t / 60), 0.35))
                continue
            # Bubble tubes: a bubble rises up each tube once a beat, tubes out of step.
            b = (cues.beat * 0.5 + k * 0.37) % 1.0
            v = 0.3 + 0.7 * max(0.0, 1 - abs(self.height[i] - b) / 0.15)
            out.append(scale(cues.palette[k % len(cues.palette)], v))
        lit = int(cues.beat * 2) % 3
        for k, i in enumerate(self.logo):
            out[i] = scale(warm, 1.0 if k % 3 == lit else 0.25)
        self._record(out, cues, cues.palette[1 % len(cues.palette)])
        return out

    def tubes(self, cues, snap, t, dt):
        out = self.marquee(cues, snap, t, dt)
        fill = 0.3 + 0.7 * cues.level
        for i, h in self.height.items():
            if h > fill:
                out[i] = scale(out[i], 0.25)
        for i in self.logo:
            out[i] = scale(cues.palette[0], 0.7 + 0.3 * cues.pulse["drive"])
        return out

    def eq(self, cues, snap, t, dt):
        bands = snap.resample(16)
        beat = 0.3 * cues.pulse["kick"]
        bands = [min(1.0, v * (0.5 + 0.5 * cues.level) + (beat if k < 3 else 0.0)) for k, v in enumerate(bands)]
        return [tuple(map(float, c)) for c in self.eq_look.frame(bands, dt)[:accent.UPPER]]

    def ribbon(self, cues, snap, t, dt):
        self.ribbon_clock += dt * (0.5 + 1.0 * cues.level)
        frame = self.ribbon_look.frame(self.ribbon_clock, swell=0.7 + 0.6 * cues.level,
                                       ignite=cues.pulse["kick"] > 0.95)
        return [tuple(map(float, c)) for c in frame[:accent.UPPER]]


    def arena(self, cues, snap, t, dt):
        return self.arena_look.frame(cues, dt)


class Foot(Scenes):
    ROWS, COLS = accent.FOOT_ROWS, accent.FOOT_COLS

    def __init__(self):
        super().__init__(accent.FOOT)
        # out[] is in chain order; grid() maps (row, col) to it.
        self.at = [[accent.foot_index(r, c) - accent.UPPER for c in range(self.COLS)] for r in range(self.ROWS)]
        self.colour_bar = None
        self.colour_n = 0
        self.arena_look = arena.FootArena()

    def _grid(self, fn):
        out = [BLACK] * self.size
        for r in range(self.ROWS):
            for c in range(self.COLS):
                out[self.at[r][c]] = fn(r, c)
        return out

    def _bar_colour(self, cues):
        bar = int(cues.beat // 4)
        if bar != self.colour_bar:
            self.colour_bar, self.colour_n = bar, self.colour_n + 1
        return cues.palette[self.colour_n % len(cues.palette)]

    def _build(self, frame, cues):
        """A big build in a strobing style: the footlights punch white on the beat."""
        if cues.style.strobe >= 2 and cues.tension > 0.5:
            k = cues.pulse["kick"] * (cues.tension - 0.5) * 1.6
            frame = [lerp(c, WHITE, k) for c in frame]
        return frame

    def glow(self, cues, snap, t, dt):
        v = 0.25 + 0.45 * cues.level
        drift = t / 50
        return self._grid(lambda r, c: scale(along(cues.palette, c / self.COLS * 0.7 + drift), v * (1 - 0.15 * r)))

    def pulse(self, cues, snap, t, dt):
        colour = self._bar_colour(cues)
        v = 0.06 + 0.94 * cues.pulse["drive"]
        return self._build(self._grid(lambda r, c: scale(colour, v * (1 - 0.1 * r))), cues)

    def rows(self, cues, snap, t, dt):
        kick, snare = cues.pulse["kick"], cues.pulse["snare"]
        front, back = cues.palette[0], cues.palette[1 % len(cues.palette)]
        return self._build(self._grid(lambda r, c: scale(front, 0.05 + 0.95 * kick) if r < 2
                                      else scale(back, 0.05 + 0.95 * snare)), cues)

    def chase(self, cues, snap, t, dt):
        pos = (steps(cues) % 8) * self.COLS / 8
        colour = self._bar_colour(cues)
        boost = 0.5 + 0.5 * cues.pulse["drive"]

        def px(r, c):
            behind = (pos - c) % self.COLS
            v = max(0.0, 1 - behind / 7) * boost
            return scale(colour, max(0.06, v))
        return self._build(self._grid(px), cues)

    def checker(self, cues, snap, t, dt):
        flip = int(steps(cues)) % 2
        v = 0.35 + 0.65 * cues.pulse["drive"]

        def px(r, c):
            tile = c // 5 + r // 2
            on = (tile + flip) % 2 == 0
            return scale(cues.palette[(c // 5 + flip) % len(cues.palette)], v if on else 0.04)
        return self._grid(px)

    def offbeat(self, cues, snap, t, dt):
        colour = self._bar_colour(cues)
        skank, kick = cues.pulse["offbeat"], cues.pulse["kick"]
        return self._grid(lambda r, c: scale(colour, 0.08 + 0.92 * skank) if r >= 1
                          else scale(cues.palette[-1], 0.1 + 0.6 * kick))

    def arena(self, cues, snap, t, dt):
        return self.arena_look.frame(cues, dt)


class Bar(Scenes):
    def __init__(self):
        super().__init__(matrix.NUM_PIXELS)
        self.analyzer = bar_spectrum.Analyzer()
        self.ribbon_look = bar_ribbon.Ribbon()
        self.ribbon_clock = 0.0
        self.peak = 0.0
        self.peaks = [0.0] * matrix.COLS
        self.rnd = random.Random(3)
        self.tom, self.tom_ref = 0.0, 0.0
        self.history, self.trace_clock = [0.0] * matrix.COLS, 0.0
        self.ripples, self.drops, self.sparks_ = [], [], []
        self.burst_held = False
        self.heat = [0.0] * matrix.COLS
        self.arena_look = arena.BarArena()

    def spectrum(self, cues, snap, t, dt):
        """The classic analyzer, in the song's palette: bass on the left,
        each column filling bottom up, a white peak falling slowly."""
        gain_ = 0.5 + 0.5 * cues.level
        cols = [min(1.0, v * gain_) for v in snap.resample(matrix.COLS)]
        self.peaks = [max(v, p - dt * 0.8) for v, p in zip(cols, self.peaks)]
        out = [BLACK] * self.size
        for col, v in enumerate(cols):
            colour = along(cues.palette, col / matrix.COLS * 0.9)
            lit = v * matrix.ROWS
            top = min(matrix.ROWS - 1, int(self.peaks[col] * matrix.ROWS))
            for row in range(matrix.ROWS):
                k = max(0.0, min(1.0, lit - row))
                out[matrix.index(col, row)] = scale(colour, 0.05 + 0.95 * k)
            if self.peaks[col] > 0.1 and top >= int(lit):
                out[matrix.index(col, top)] = scale(WHITE, 0.7)
        return out

    def classic(self, cues, snap, t, dt):
        """The original rainbow analyzer (themes/volume_bar_theme3.py)."""
        gain_ = 0.5 + 0.5 * cues.level
        cols = [min(1.0, v * gain_) for v in snap.resample(matrix.COLS)]
        return [tuple(float(v) for v in c) for c in self.analyzer.frame(cols, dt)]

    def vu(self, cues, snap, t, dt):
        # The level is against the song's own loud parts, so a loud song sits
        # near the top: spread its upper half across the bar, plus the drive.
        swing = max(0.0, min(1.0, (cues.level - 0.45) / 0.55))
        level = swing if not cues.style.beat_locked else min(1.0, 0.75 * swing + 0.3 * cues.pulse["drive"])
        self.peak = max(level, self.peak - dt * 0.5)
        half = matrix.COLS / 2
        out = [BLACK] * self.size
        for col in range(matrix.COLS):
            d = abs(col + 0.5 - half) / half          # 0 centre .. 1 edge
            colour = along(cues.palette, d * 0.9)
            on = d <= level
            hold = abs(d - self.peak) < 1 / half
            for row in range(matrix.ROWS):
                out[matrix.index(col, row)] = colour if on else scale(WHITE, 0.6) if hold else scale(colour, 0.05)
        return out

    def ribbon(self, cues, snap, t, dt):
        self.ribbon_clock += dt * (0.5 + 1.0 * cues.level)
        frame = self.ribbon_look.frame(self.ribbon_clock, swell=0.7 + 0.6 * cues.level,
                                       ignite=cues.pulse["kick"] > 0.95)
        return [tuple(float(v) for v in c) for c in frame]

    # The bar as a canvas of chunky blocks (its pixels are twice as tall as
    # wide, so 2 columns x 1 row reads square).

    def _blank(self):
        return [BLACK] * self.size

    def _block(self, out, cols, rows, colour):
        for col in cols:
            for row in rows:
                out[matrix.index(col, row)] = colour

    def drumkit(self, cues, snap, t, dt):
        """A drum kit seen from above, each drum a block hit with its own beat:
        hi-hat, snare, the kick big in the middle, a tom, the crash cymbal."""
        mid = sum(snap.bands[10:18]) / 8 if snap.bands else 0.0
        if mid - self.tom_ref > 0.18 and self.tom < 0.3:
            self.tom = 1.0                                     # a jump in the low mids: a tom
        self.tom_ref += (mid - self.tom_ref) * min(1.0, dt * 4)
        self.tom = max(0.0, self.tom - dt / 0.2)
        p, pal = cues.pulse, cues.palette
        kit = [  # (columns, rows, pulse, colour)
            (range(0, 6), range(1, 5), p["hat"], lerp(pal[1 % len(pal)], WHITE, 0.5)),
            (range(7, 13), range(0, 4), p["snare"], pal[0]),
            (range(14, 23), range(0, 5), p["kick"], pal[2 % len(pal)]),
            (range(24, 28), range(2, 5), self.tom, pal[1 % len(pal)]),
            (range(29, 36), range(1, 5), p["crash"], lerp(pal[-1], WHITE, 0.6)),
        ]
        out = self._blank()
        for cols, rows, hit, colour in kit:
            self._block(out, cols, rows, scale(colour, 0.07 + 0.93 * hit))
        return out

    def tiles(self, cues, snap, t, dt):
        """The spectrum as six fat glowing tiles, bass on the left."""
        bands = snap.resample(6)
        out = self._blank()
        for k, v in enumerate(bands):
            v = min(1.0, v * (0.5 + 0.6 * cues.level))
            colour = along(cues.palette, k / 6)
            self._block(out, range(k * 6, k * 6 + 5), range(matrix.ROWS), scale(colour, 0.05 + 0.95 * v * v))
        return out

    def mirror(self, cues, snap, t, dt):
        """The spectrum folded in half: bass in the middle, highs at both ends."""
        half = matrix.COLS // 2
        cols = snap.resample(half)
        out = self._blank()
        for k, v in enumerate(cols):
            v = min(1.0, v * (0.5 + 0.6 * cues.level))
            colour = along(cues.palette, k / half)
            for col in (half - 1 - k, half + k):
                for row in range(matrix.ROWS):
                    lit = max(0.0, min(1.0, v * matrix.ROWS - row))
                    out[matrix.index(col, row)] = scale(colour, 0.04 + 0.96 * lit)
        return out

    def bounce(self, cues, snap, t, dt):
        """A ball landing on every beat, crossing the bar once a bar."""
        bar = cues.beat / 4
        x = (bar % 1.0) * 2
        x = (x if x <= 1 else 2 - x) * (matrix.COLS - 2)          # there and back
        height = abs(math.sin(math.pi * (cues.beat % 1.0))) * (matrix.ROWS - 1)
        out = [scale(cues.palette[-1], 0.04 + 0.2 * cues.pulse["kick"]) if r == 0 else BLACK
               for c in range(matrix.COLS) for r in range(matrix.ROWS)]
        colour = cues.palette[int(bar) % len(cues.palette)]
        for col in range(matrix.COLS):
            for row in range(matrix.ROWS):
                d = math.hypot((col - x) / 1.6, row - height)
                if d < 1.4:
                    out[matrix.index(col, row)] = scale(colour, 1 - d / 1.4)
        return out

    def trace(self, cues, snap, t, dt):
        """The last few seconds scrolling by, like a heart monitor."""
        self.trace_clock += dt
        while self.trace_clock >= 1 / 12:                      # 12 columns a second: 3 s on the bar
            self.trace_clock -= 1 / 12
            self.history = self.history[1:] + [max(cues.level * 0.6, cues.pulse["kick"])]
        out = self._blank()
        for col, v in enumerate(self.history):
            colour = along(cues.palette, col / matrix.COLS)
            fresh = 0.35 + 0.65 * col / matrix.COLS              # older moments fade
            for row in range(matrix.ROWS):
                lit = max(0.0, min(1.0, v * matrix.ROWS - row))
                out[matrix.index(col, row)] = scale(colour, fresh * lit)
        return out

    def ripple(self, cues, snap, t, dt):
        """Every kick sends a ring out from the middle."""
        if cues.pulse["kick"] > 0.95 and (not self.ripples or self.ripples[-1][0] > 0.15):
            self.ripples.append([0.0, cues.palette[len(self.ripples) % len(cues.palette)]])
        for r in self.ripples:
            r[0] += dt
        self.ripples = [r for r in self.ripples if r[0] < 0.7]
        out = self._blank()
        half = matrix.COLS / 2
        for col in range(matrix.COLS):
            d = abs(col + 0.5 - half)
            c = BLACK
            for age, colour in self.ripples:
                ring = age / 0.7 * half
                k = max(0.0, 1 - abs(d - ring) / 2.2) * (1 - age / 0.7)
                c = (max(c[0], colour[0] * k), max(c[1], colour[1] * k), max(c[2], colour[2] * k))
            for row in range(matrix.ROWS):
                out[matrix.index(col, row)] = c
        return out

    def counter(self, cues, snap, t, dt):
        """Four blocks counting the bar: 1, 2, 3, 4."""
        out = self._blank()
        frac = cues.beat % 1.0
        for k in range(4):
            colour = lerp(cues.palette[k % len(cues.palette)], WHITE, 0.4 if k == 0 else 0.0)
            v = max(0.0, 1 - frac / 0.6) if k == cues.bar_beat else 0.06
            self._block(out, range(k * 9, k * 9 + 8), range(matrix.ROWS), scale(colour, v))
        return out

    def comet(self, cues, snap, t, dt):
        """A comet streaking across on each beat, its tail behind it."""
        n = int(cues.beat)
        head = (cues.beat % 1.0) * (matrix.COLS + 8)
        backwards = n % 2
        colour = cues.palette[n % len(cues.palette)]
        out = self._blank()
        for col in range(matrix.COLS):
            c = matrix.COLS - 1 - col if backwards else col
            behind = head - c
            if 0 <= behind < 9:
                k = (1 - behind / 9) ** 2
                for row in range(matrix.ROWS):
                    wave = 1 - abs(row - 2 + math.sin(c * 0.5)) / 3
                    out[matrix.index(col, row)] = scale(lerp(colour, WHITE, k * 0.5), k * max(0.2, wave))
        return out

    def fire(self, cues, snap, t, dt):
        """Flames rising with the music: white-hot at the base, the palette above."""
        for col in range(matrix.COLS):
            band = snap.bands[int(col / matrix.COLS * 24)] if snap.bands else 0.0
            target = 0.25 + 0.75 * min(1.0, band * (0.5 + 0.6 * cues.level)) + 0.3 * cues.pulse["drive"]
            target *= 0.75 + 0.5 * self.rnd.random()
            self.heat[col] += (target - self.heat[col]) * min(1.0, dt * 12)
        out = self._blank()
        for col, h in enumerate(self.heat):
            for row in range(matrix.ROWS):
                k = max(0.0, min(1.0, h * matrix.ROWS - row))
                base = lerp(cues.palette[-1], (255.0, 230.0, 160.0), 1 - row / matrix.ROWS)
                out[matrix.index(col, row)] = scale(base, k * (1 - 0.12 * row))
        return out

    def rain(self, cues, snap, t, dt):
        """Each hi-hat drops a raindrop; the kick lights the puddle below."""
        if fresh_hat(self, cues):
            self.drops.append([self.rnd.randrange(matrix.COLS), matrix.ROWS - 0.01,
                               cues.palette[self.rnd.randrange(len(cues.palette))]])
        for d in self.drops:
            d[1] -= dt * 9                                    # rows a second
        self.drops = [d for d in self.drops if d[1] > -1]
        out = [scale(cues.palette[0], 0.03 + 0.25 * cues.pulse["kick"]) if r == 0 else BLACK
               for c in range(matrix.COLS) for r in range(matrix.ROWS)]
        for col, y, colour in self.drops:
            for row in range(matrix.ROWS):
                k = max(0.0, 1 - abs(row - y) / 1.2)
                if k:
                    out[matrix.index(col, row)] = lerp(out[matrix.index(col, row)], colour, k)
        return out

    def sparks(self, cues, snap, t, dt):
        """Big hits burst into sparks; hi-hats throw single ones."""
        if cues.pulse["crash"] > 0.95 and not self.burst_held:
            x = self.rnd.uniform(6, matrix.COLS - 6)
            for _ in range(14):
                a = self.rnd.uniform(0, 2 * math.pi)
                v = self.rnd.uniform(8, 22)
                self.sparks_.append([x, 2.0, v * math.cos(a), v * math.sin(a) * 0.4, 1.0, WHITE])
        self.burst_held = cues.pulse["crash"] > 0.95
        if fresh_hat(self, cues):
            self.sparks_.append([self.rnd.uniform(0, matrix.COLS), self.rnd.uniform(0, 4), 0.0, 0.0, 0.6,
                                 cues.palette[self.rnd.randrange(len(cues.palette))]])
        for s in self.sparks_:
            s[0] += s[2] * dt
            s[1] += s[3] * dt
            s[4] -= dt / 0.6
            s[5] = lerp(s[5], cues.palette[0], min(1.0, dt * 3))   # cooling from white to the palette
        self.sparks_ = [s for s in self.sparks_ if s[4] > 0]
        out = self._blank()
        for x, y, _, _, life, colour in self.sparks_:
            col, row = int(x), int(round(y))
            if 0 <= col < matrix.COLS and 0 <= row < matrix.ROWS:
                i = matrix.index(col, row)
                out[i] = lerp(out[i], colour, life)
        return out

    def arena(self, cues, snap, t, dt):
        return self.arena_look.frame(cues, dt)


class Keys:
    """The keypad's part: RGBW per pixel. Not a Scenes: one look per style."""

    def __init__(self, seed=5):
        self.rnd = random.Random(seed)
        self.spark = {}
        self.arena_state = {}
        self.cues = None

    def frame(self, cues, snap, dt):
        cues = self.cues = cues or self.cues
        if cues is None:
            return {i: (0, 0, 0, 0) for i in range(buttons.NUM_PIXELS)}
        name = cues.scene["keys"]
        if name == "arena":
            k = 1 - cues.black
            return {i: tuple(int(v * k) for v in c) for i, c in
                    arena.keys(cues, self.arena_state, dt, ROW_X, WHITE_KEYS, buttons.NUM_PIXELS).items()}
        out = {}
        if name == "sparkle":
            for i in list(self.spark):
                self.spark[i] -= dt / 0.25
                if self.spark[i] <= 0:
                    del self.spark[i]
            if fresh_hat(self, cues):
                self.spark[self.rnd.choice(list(ROW_X))] = 1.0
            for i, x in ROW_X.items():
                c = scale(along(cues.palette, x), 0.18)
                out[i] = lerp(c, WHITE, self.spark.get(i, 0.0))
        elif name == "eq":
            bands = snap.resample(10)
            for i, x in ROW_X.items():
                v = 0.08 + 0.92 * min(1.0, bands[round(x * 9)] * (0.5 + 0.5 * cues.level))
                out[i] = scale(along(cues.palette, x), v)
        elif name == "pulse":
            v = 0.15 + 0.85 * cues.pulse["drive"]
            for i, x in ROW_X.items():
                out[i] = scale(along(cues.palette, x), v)
        else:  # glow
            v = 0.2 + 0.5 * cues.level
            for i, x in ROW_X.items():
                out[i] = scale(along(cues.palette, x + snap.level * 0.1), v)
        g = gain(cues, "keys") * (1 - cues.black)
        f = cues.flash * 0.8
        rgbw = {i: (int(r * g), int(gr * g), int(b * g), int(255 * f)) for i, (r, gr, b) in out.items()}
        w = int((40 + 110 * cues.pulse["kick"]) * (1 - cues.black))
        for i in WHITE_KEYS:
            rgbw[i] = (0, 0, 0, max(w, int(255 * f)))
        for i in range(buttons.NUM_PIXELS):
            rgbw.setdefault(i, (0, 0, 0, 0))
        return rgbw


class AccentShow:
    """The accent chain's half of the show: the upper panel and the footlights."""

    def __init__(self, pos):
        self.upper, self.foot = Upper(pos), Foot()
        self.cues = None

    def frame(self, t, dt, snap):
        # Fading back to idle there are no new cues: hold the last ones.
        cues = self.cues = snap.cues or self.cues
        if cues is None:
            return [(0, 0, 0)] * accent.NUM_PIXELS
        up = finish(self.upper.frame(cues.scene["upper"], cues, snap, t, dt), cues, "upper")
        ft = finish(self.foot.frame(cues.scene["foot"], cues, snap, t, dt), cues, "foot")
        return [(int(r), int(g), int(b)) for r, g, b in up + ft]


class BarShow:
    def __init__(self):
        self.bar = Bar()
        self.cues = None

    def frame(self, t, dt, snap):
        cues = self.cues = snap.cues or self.cues
        if cues is None:
            return [(0, 0, 0)] * matrix.NUM_PIXELS
        out = finish(self.bar.frame(cues.scene["bar"], cues, snap, t, dt), cues, "bar")
        return [(int(r), int(g), int(b)) for r, g, b in out]
