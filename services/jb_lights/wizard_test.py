"""The setup wizard's light checks (docs/Setup Wizard — Plan.md, steps 11
and 12), run as root by jb-setupctl in a short-lived unit while jb-lights
is stopped (it restarts jb-lights when this ends):

    python -m services.jb_lights.wizard_test identify buttons|accent|levelbar [--seconds 90]
    python -m services.jb_lights.wizard_test load buttons,accent,levelbar [--seconds 30]

identify: the chain soft white, then one bright spot walking along it in
chain order, over and over, so the owner can see it's all lit and in order.
load: every named chain as bright as its software cap allows, the Oct 20
power test (accent at accent.MAX_AMPS, the level bar at matrix.MAX_AMPS
through its limiter, the buttons white): the admin watches the board's
voltage meanwhile. Frames are resent continuously; all off at the end, on
Ctrl-C and on SIGTERM.
"""

import argparse
import signal
import sys
import time

from services.jb_lights import accent, buttons, matrix
from services.jb_lights.ws2812_spi import AMPS_PER_CHANNEL_FULL

FRAME_S = 0.05
SOFT = 40           # identify's resting white, per channel
SPOT = 255
SPOT_STEP_S = 0.12  # a walking spot moves one LED (or bar column) this often
BUTTONS_LOAD = 128  # the buttons' share of the load test: small either way (23 LEDs)


def color(r, g, b, w=0):
    return (w << 24) | (r << 16) | (g << 8) | b


def accent_load_level() -> int:
    """The per-channel level at which every accent LED white draws the
    chain's cap: 6 A / (349 x 3 channels x 20 mA) of 255, about 73."""
    return min(255, int(accent.MAX_AMPS / (accent.NUM_PIXELS * 3 * AMPS_PER_CHANNEL_FULL) * 255))


def strip_frame(count: int, t: float, white: bool) -> list[int]:
    """identify for a strip: soft white with one bright spot walking."""
    spot = int(t / SPOT_STEP_S) % count
    base = color(0, 0, 0, SOFT) if white else color(SOFT, SOFT, SOFT)
    lit = color(0, 0, 0, SPOT) if white else color(SPOT, SPOT, SPOT)
    return [lit if i == spot else base for i in range(count)]


def bar_frame(t: float) -> list[tuple[int, int, int]]:
    """identify for the level bar: soft white with one bright column
    walking from the first strip to the last (frame index = col * ROWS + row)."""
    spot = int(t / SPOT_STEP_S) % matrix.COLS
    soft, lit = (SOFT, SOFT, SOFT), (SPOT, SPOT, SPOT)
    return [lit if c == spot else soft for c in range(matrix.COLS) for _ in range(matrix.ROWS)]


class Lights:
    """The chains asked for, opened from the board's drivers."""

    def __init__(self, names):
        self.names = set(names)
        self.strips = {}
        if self.names & {"buttons", "accent"}:
            from services.jb_lights import chains
            got = chains.get()
            for name in ("buttons", "accent"):
                if name in self.names and getattr(got, name, None) is not None:
                    self.strips[name] = getattr(got, name)
        self.bar = None
        if "levelbar" in self.names:
            from services.jb_lights.ws2812_spi import WS2812SPI
            self.bar = WS2812SPI(matrix.NUM_PIXELS, brightness=1.0, max_amps=matrix.MAX_AMPS)

    def show(self, name, frame):
        if name == "levelbar":
            if self.bar:
                self.bar.show(frame)
            return
        strip = self.strips.get(name)
        if strip:
            for i, c in enumerate(frame):
                strip.setPixelColor(i, c)
            strip.show()

    def off(self):
        for name, strip in self.strips.items():
            n = buttons.NUM_PIXELS if name == "buttons" else accent.NUM_PIXELS
            for i in range(n):
                strip.setPixelColor(i, 0)
            strip.show()
            if name == "buttons":
                buttons.close_strip(strip)
        if self.bar:
            self.bar.off()
            self.bar.close()


def identify(lights: Lights, name: str, seconds: float) -> None:
    start = time.monotonic()
    while (t := time.monotonic() - start) < seconds:
        if name == "levelbar":
            lights.show(name, bar_frame(t))
        else:
            count = buttons.NUM_PIXELS if name == "buttons" else accent.NUM_PIXELS
            lights.show(name, strip_frame(count, t, white=name == "buttons"))
        time.sleep(FRAME_S)


def load(lights: Lights, seconds: float) -> None:
    level = accent_load_level()
    frames = {
        "buttons": [color(BUTTONS_LOAD, BUTTONS_LOAD, BUTTONS_LOAD, BUTTONS_LOAD)] * buttons.NUM_PIXELS,
        "accent": [color(level, level, level)] * accent.NUM_PIXELS,
        "levelbar": [(255, 255, 255)] * matrix.NUM_PIXELS,     # its driver scales it to the cap
    }
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for name in lights.names:
            lights.show(name, frames[name])
        time.sleep(0.5)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("identify", "load"))
    ap.add_argument("chains")
    ap.add_argument("--seconds", type=float)
    args = ap.parse_args(argv)
    names = [n for n in args.chains.split(",") if n in ("buttons", "accent", "levelbar")]
    if not names or (args.mode == "identify" and len(names) != 1):
        ap.error("name the chain(s): buttons, accent, levelbar")
    seconds = max(1.0, min(args.seconds or (90 if args.mode == "identify" else 30), 300))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    lights = Lights(names)
    try:
        if args.mode == "identify":
            identify(lights, names[0], seconds)
        else:
            load(lights, seconds)
    except KeyboardInterrupt:
        pass
    finally:
        lights.off()


if __name__ == "__main__":
    main()
