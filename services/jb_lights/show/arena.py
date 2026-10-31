"""Arena: a dark cabinet, then VERY bright (the owner's design, 2026-10-30).

  dark    most of the song: the cabinet dark, blue sparkles dancing all
          over it, quicker with the hi-hats and the kick. The footlights
          snap between two blue bands on the beat: the outer sixths lit
          (the middle black), then the middle third lit (the outsides black)
  hush    a build rising, or the bass falling away before a drop: the
          whole cabinet fades toward black (a gap snaps it black), so the
          drop comes out of the dark
  burst   on the drop itself (or a really big hit): the whole cabinet
          flashes white, then the white breaks
          up into white and blue sparkles from the centre of the cabinet
          outwards, and settles back to the dark

Every part draws from the same functions of the cabinet's shared picture
(accent.positions, matrix.positions), so the burst spreads across the whole
cabinet from one centre. Each part has a current budget, well inside its
branch's cap; the flash gets a bigger one for a moment, still inside the
cap (tests/test_arena.py checks every frame). Bursts come at most one a
second, or a drop 0.4 s after a big hit (the director's BURST_MIN_S,
DROP_MIN_S), under the 3 flashes a second the flash-safety rule allows.
"""

import math
import random

from services.jb_lights import accent, matrix
from services.jb_lights.themes.accent_city import BRIGHTNESS as ACCENT_SCALE
from services.jb_lights.themes.accent_diner import AMPS_PER_CHANNEL_FULL

BLUE = (8.0, 26.0, 255.0)
ICE = (70.0, 120.0, 255.0)        # a lighter blue for some sparkles
WHITE = (255.0, 255.0, 255.0)
BLACK = (0.0, 0.0, 0.0)

# The dark: sparkles a second, per pixel, and how long each lasts.
SPARK_RATE, SPARK_HAT, SPARK_KICK = 0.12, 0.9, 0.35
SPARK_S = 0.35
SPARK_V = 0.85
CALM_RATE = 0.5                   # calm passages sparkle half as much
BAND_V, BAND_KICK = 0.45, 0.3     # the footlights' and the keypad's blue bands
HUSH = 0.92                       # how far a build or a breakdown fades the cabinet down

# The burst: white for FLASH_S, then the break-up runs out from the centre.
FLASH_S = 0.15
FRONT_IN_S = 24.0                 # how fast the break-up spreads, inches a second
WHITE_FADE_S = 0.6                # the white not yet broken up fades this fast
SPARKLE_S = 0.8                   # the burst's sparkles fade over this long
BURST_S = 3.5                     # after this the burst is over

# Where the burst starts: the middle of the lit cabinet (inches from the
# top-left of the upper panel), between the panel and the level bar.
IN_X, IN_Y = accent.UPPER_WIDTH_IN, accent.UPPER_WIDTH_IN / accent.UPPER_ASPECT
CENTRE = (0.5 * IN_X, 15.0)

# Current budgets in amps, per part, at the chain's own brightness. The
# accent chain (upper + footlights) is capped at 6 A, the level bar at 4 A.
UPPER_A, FOOT_A, BAR_A = 2.5, 1.5, 2.4
UPPER_FLASH_A, FOOT_FLASH_A, BAR_FLASH_A = 3.2, 1.8, 3.2


def scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def lerp(a, b, k):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def brighter(a, b):
    return a if sum(a) >= sum(b) else b


def hush(cues):
    """How far to fade the dark down: a riser building, or a breakdown."""
    return HUSH * max(cues.tension, cues.cool)


def bands(cues, x):
    """The band pattern at x (0..1 across): the outer sixths lit, then on the
    next beat the middle third; on fast songs every other beat."""
    per = 2 if cues.tempo > 140 else 1
    if int(cues.beat // per) % 2 == 1:
        return 1 / 3 < x < 2 / 3
    return x < 1 / 6 or x > 5 / 6


def band_level(cues):
    v = BAND_V + BAND_KICK * cues.pulse["kick"]
    return v * 0.6 if cues.tier == "calm" else v


def rand(n, gid):
    """A fixed 0..1 for this moment and pixel."""
    v = math.sin(n * 12.9898 + gid * 78.233) * 43758.5453
    return v - math.floor(v)


class Arena:
    """One instance per part. pos: the part's pixels in the shared picture."""

    def __init__(self, pos, gid0=0, budget=UPPER_A, flash_budget=UPPER_FLASH_A, chain_scale=ACCENT_SCALE, seed=1):
        self.n = len(pos)
        self.gid0 = gid0
        self.budget, self.flash_budget, self.chain_scale = budget, flash_budget, chain_scale
        self.dist = [math.hypot(x * IN_X - CENTRE[0], y * IN_Y - CENTRE[1]) for x, y in pos]
        self.rnd = random.Random(seed)
        self.sparks = {}              # pixel -> [life 1..0, colour]

    def sparkle(self, cues, dt):
        """The dark's sparkles: born at random, quicker with the hats and the kick."""
        for i in list(self.sparks):
            self.sparks[i][0] -= dt / SPARK_S
            if self.sparks[i][0] <= 0:
                del self.sparks[i]
        rate = SPARK_RATE + SPARK_HAT * cues.pulse["hat"] + SPARK_KICK * cues.pulse["kick"]
        if cues.tier == "calm":
            rate *= CALM_RATE
        born = rate * dt * self.n
        while born > 0:
            if self.rnd.random() < born:
                self.sparks[self.rnd.randrange(self.n)] = [1.0, ICE if self.rnd.random() < 0.3 else BLUE]
            born -= 1
        out = [BLACK] * self.n
        for i, (life, colour) in self.sparks.items():
            out[i] = scale(colour, SPARK_V * life * life)
        return out

    def burst(self, a, i):
        """Pixel i, a seconds into a burst: white, then sparkling away from the centre."""
        if a < FLASH_S:
            return WHITE
        since = a - FLASH_S
        passed = since - self.dist[i] / FRONT_IN_S          # how long ago the break-up reached it
        if passed < 0:
            return scale(WHITE, math.exp(-since / WHITE_FADE_S))
        if rand(int(a * 24), self.gid0 + i) > 0.55 * math.exp(-passed / SPARKLE_S):
            return BLACK
        colour = lerp(WHITE, BLUE if rand(7, self.gid0 + i) < 0.6 else ICE, min(1.0, passed / 0.5))
        return scale(colour, math.exp(-passed / SPARKLE_S))

    def base(self, cues, dt):
        return self.sparkle(cues, dt)

    def frame(self, cues, dt):
        out = self.base(cues, dt)
        quiet = hush(cues)
        if quiet > 0:
            out = [scale(c, 1 - quiet) for c in out]
        a = cues.since_burst
        if a < BURST_S:
            out = [brighter(c, self.burst(a, i)) for i, c in enumerate(out)]
        return self.fit(out, a)

    def amps(self, frame):
        return sum(r + g + b for r, g, b in frame) / 255 * AMPS_PER_CHANNEL_FULL * self.chain_scale

    def fit(self, frame, since_burst=1e9):
        """Dim a frame that would draw more than this part's budget (more for the flash)."""
        k = max(0.0, min(1.0, 1 - (since_burst - FLASH_S) / 1.0))
        budget = self.budget + (self.flash_budget - self.budget) * k
        a = self.amps(frame)
        if a <= budget:
            return frame
        return [scale(c, budget / a) for c in frame]


class UpperArena(Arena):
    def __init__(self, pos):
        super().__init__(pos[:accent.UPPER])


class FootArena(Arena):
    """The footlights: bands snapping on the beat, sparkles over them."""

    def __init__(self):
        super().__init__(accent.positions()[accent.UPPER:], gid0=accent.UPPER,
                         budget=FOOT_A, flash_budget=FOOT_FLASH_A, seed=2)
        self.x = [0.0] * accent.FOOT
        for r in range(accent.FOOT_ROWS):
            for c in range(accent.FOOT_COLS):
                self.x[accent.foot_index(r, c) - accent.UPPER] = (c + 0.5) / accent.FOOT_COLS

    def base(self, cues, dt):
        v = band_level(cues)
        out = self.sparkle(cues, dt)
        for i, x in enumerate(self.x):
            if bands(cues, x):
                out[i] = brighter(out[i], scale(BLUE, v))
        return out


class BarArena(Arena):
    def __init__(self):
        super().__init__(matrix.positions(), gid0=accent.NUM_PIXELS, budget=BAR_A,
                         flash_budget=BAR_FLASH_A, chain_scale=1.0, seed=3)


def keys(cues, state, dt, row_x, white_keys, n_pixels):
    """The keypad, RGBW: the letters and digits do the footlights' blue bands
    with sparkles on top; SINGLE, ALBUM and RESET (behind filters that block
    blue) pulse white with the kick; everything goes white in a burst."""
    rnd = state.setdefault("rnd", random.Random(4))
    sparks = state.setdefault("sparks", {})
    for i in list(sparks):
        sparks[i] -= dt / SPARK_S
        if sparks[i] <= 0:
            del sparks[i]
    rate = (SPARK_RATE + SPARK_HAT * cues.pulse["hat"]) * len(row_x)
    if rnd.random() < rate * dt:
        sparks[rnd.choice(list(row_x))] = 1.0
    quiet = 1 - hush(cues)
    a = cues.since_burst
    white = 0.0 if a >= BURST_S else 1.0 if a < FLASH_S else math.exp(-(a - FLASH_S) / WHITE_FADE_S)
    band = band_level(cues)
    out = {}
    for i in range(n_pixels):
        life = sparks.get(i, 0.0)
        colour = scale(ICE, SPARK_V * life * life)
        if i in row_x and bands(cues, row_x[i]):
            colour = brighter(colour, scale(BLUE, band))
        colour = scale(colour, quiet)
        out[i] = (int(colour[0]), int(colour[1]), int(colour[2]), int(255 * white))
    for i in white_keys:
        w = (20 + 110 * cues.pulse["kick"]) * quiet
        out[i] = (0, 0, 0, int(max(w, 255 * white)))
    return out
