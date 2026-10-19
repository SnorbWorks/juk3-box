"""The accent chain's controller (upper panel + footlights), run by the
jb-lights service in its own thread.

Follows the music via the shared AudioFeed (jb-analyzer's stream):
  idle    accent-city, the city at night
  show    the light show (show/parts.py): the upper panel tells the song's
          story, the footlights carry its rhythm, as the director cues
  eq      accent-eq, the columns as a graphic EQ of the real bands
  ribbon  accent-ribbon, flowing faster and swelling with the music, its hot
          spots flaring on the beat
Looks crossfade; in eq and ribbon a drop flashes the whole chain white for a
moment (the show flashes on its own cues). Without
the analyzer (or a feed) the city simply stays on.

The chain shares its controller with the buttons (pwm_chains), and the
accent branch's 6A cap is applied every frame (accent_diner.limit).
"""

import threading
import time

from rpi_ws281x import Color

from services.jb_lights import accent, pwm_chains
from services.jb_lights.show.parts import AccentShow
from services.jb_lights.themes.accent_city import BRIGHTNESS, City
from services.jb_lights.themes.accent_diner import limit
from services.jb_lights.themes.accent_eq import EQ
from services.jb_lights.themes.accent_ribbon import AccentRibbon

FPS = 40
FADE_S = 1.5              # crossfade between looks
DROP_FLASH_S = 0.8        # a drop: white flash fading over this long
DROP_FLASH = 0.6          # how far toward white at the flash's peak
WHITE = (255, 255, 255)


def mix(a, b, k):
    if k <= 0:
        return a
    if k >= 1:
        return b
    return [(int(x0 + (x1 - x0) * k), int(y0 + (y1 - y0) * k), int(z0 + (z1 - z0) * k))
            for (x0, y0, z0), (x1, y1, z1) in zip(a, b)]


class Looks:
    """The accent chain's looks, driven by audio snapshots; pure (no LEDs)."""

    def __init__(self, pos):
        self.city = City(pos)
        self.eq = EQ(pos)
        self.ribbon = AccentRibbon(pos)
        self.show = AccentShow(pos)
        self.ribbon_clock = 0.0
        self.current, self.previous, self.fade = "idle", None, 1.0

    def _render(self, look, t, dt, snap):
        if look == "show":
            return self.show.frame(t, dt, snap)
        if look == "eq":
            bands = snap.resample(16)
            gain = 0.5 + 0.5 * snap.level
            beat = 0.3 * snap.beat()
            bands = [min(1.0, v * gain + (beat if k < 3 else 0.0)) for k, v in enumerate(bands)]
            return self.eq.frame(bands, dt)
        if look == "ribbon":
            self.ribbon_clock += dt * (0.5 + 1.0 * snap.level)
            return self.ribbon.frame(self.ribbon_clock, swell=0.7 + 0.6 * snap.level,
                                     ignite=snap.beat_age <= dt)
        return self.city.frame(t, dt)

    def frame(self, t, dt, snap):
        if snap.look != self.current:
            self.previous, self.current, self.fade = self.current, snap.look, 0.0
        self.fade = min(1.0, self.fade + dt / FADE_S)
        out = self._render(self.current, t, dt, snap)
        if self.fade < 1.0 and self.previous:
            out = mix(self._render(self.previous, t, dt, snap), out, self.fade)
        if snap.look in ("eq", "ribbon") and snap.drop_age < DROP_FLASH_S:
            k = DROP_FLASH * (1 - snap.drop_age / DROP_FLASH_S) ** 2
            out = mix(out, [WHITE] * len(out), k)
        return out


def run(stop=None, feed=None):
    stop = stop or threading.Event()
    chain = pwm_chains.get().accent
    looks = Looks(accent.positions())
    city_only = feed is None
    start = last = time.monotonic()
    try:
        while not stop.is_set():
            now = time.monotonic()
            t, dt = now - start, now - last
            last = now
            frame = looks.city.frame(t, dt) if city_only else looks.frame(t, dt, feed.snapshot(now))
            for i, (r, g, b) in enumerate(limit(frame, BRIGHTNESS)):
                chain.setPixelColor(i, Color(r, g, b))
            chain.show()
            stop.wait(max(0.0, 1 / FPS - (time.monotonic() - now)))
    finally:
        for i in range(accent.NUM_PIXELS):
            chain.setPixelColor(i, Color(0, 0, 0))
        chain.show()
