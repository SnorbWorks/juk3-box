"""volume_bar-stripes: the bar's idle look beside the city at night -- each
strip its own colour, repeating red, green, blue, amber, cyan, magenta.

The bring-up test pattern (bartest strips), which the user liked enough to
keep (2026-10-19). One colour per pixel at 20% draws ~0.7 A, a fraction of
the old all-white idle's 4 A, so the bar runs cooler.
"""

from services.jb_lights import matrix

BRIGHTNESS = 0.20
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 170, 0), (0, 255, 255), (255, 0, 255)]


class Stripes:
    def __init__(self):
        self._frame = [tuple(int(v * BRIGHTNESS) for v in COLORS[c % len(COLORS)])
                       for c in range(matrix.COLS) for _ in range(matrix.ROWS)]

    def frame(self, t, dt):
        return self._frame
