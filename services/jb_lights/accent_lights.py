"""The accent chain's controller (upper panel + footlights), run by the
jb-lights service in its own thread.

Shows accent-city (the city at night) for now. The look while music plays
(accent-eq, accent-ribbon, or switching between them) is the user's call
once real audio levels exist; until then the city stays on.

The chain shares its controller with the buttons (pwm_chains), and the
accent branch's 6A cap is applied every frame (accent_diner.limit).
"""

import threading
import time

from rpi_ws281x import Color

from services.jb_lights import accent, pwm_chains
from services.jb_lights.themes.accent_city import BRIGHTNESS, City
from services.jb_lights.themes.accent_diner import limit

FPS = 40


def run(stop=None):
    stop = stop or threading.Event()
    chain = pwm_chains.get().accent
    city = City(accent.positions())
    start = last = time.monotonic()
    try:
        while not stop.is_set():
            now = time.monotonic()
            frame = city.frame(now - start, now - last)
            last = now
            for i, (r, g, b) in enumerate(limit(frame, BRIGHTNESS)):
                chain.setPixelColor(i, Color(r, g, b))
            chain.show()
            stop.wait(max(0.0, 1 / FPS - (time.monotonic() - now)))
    finally:
        for i in range(accent.NUM_PIXELS):
            chain.setPixelColor(i, Color(0, 0, 0))
        chain.show()
