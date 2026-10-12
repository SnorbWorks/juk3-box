"""volume_bar-idle: the bar's resting look -- every pixel white at 60%.

The user's choice (2026-10-11) for the default theme. Themes 1-3 will be
picked dynamically once the audio mood analyzer exists.

All 180 px white at 60% estimates to ~6.5 A, over the 4 A level-bar cap, so
the driver's limiter holds it at the cap (~37% white) -- the brightest an
all-white bar can be on this branch.
"""

from services.jb_lights import matrix

BRIGHTNESS = 0.60
WHITE = (255, 255, 255)


class Idle:
    def __init__(self):
        px = tuple(int(v * BRIGHTNESS) for v in WHITE)
        self._frame = [px] * matrix.NUM_PIXELS

    def frame(self, t, dt):
        return self._frame
