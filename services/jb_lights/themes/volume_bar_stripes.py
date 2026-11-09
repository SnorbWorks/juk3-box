"""volume_bar-stripes: the bar's idle look beside the city at night -- each
strip its own colour, repeating red, green, blue, amber, cyan, magenta.

The bring-up test pattern (bartest strips), which the user liked enough to
keep (2026-10-19). Now the stripes march slowly across the bar and a bright
blink travels along them, so the colour bars move and blink a little more
(user, 2026-11-08). One colour per pixel averages ~20%, a fraction of the old
all-white idle's 4 A, so the bar still runs cool.
"""

import math

from services.jb_lights import matrix

BRIGHTNESS = 0.20
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 170, 0), (0, 255, 255), (255, 0, 255)]
SPEED = 0.6            # columns the stripes march per second
BLINK_HZ = 1.1        # how fast the blink travels along the bar
BLINK = (0.4, 1.0)    # the blink's brightness, trough to crest


class Stripes:
    def frame(self, t, dt):
        frame = matrix.blank()
        shift = t * SPEED
        lo, hi = BLINK
        for c in range(matrix.COLS):
            base = COLORS[int(c - shift) % len(COLORS)]
            blink = lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * BLINK_HZ * t - c * 0.5))
            px = tuple(int(v * BRIGHTNESS * blink) for v in base)
            for r in range(matrix.ROWS):
                frame[matrix.index(c, r)] = px
        return frame
