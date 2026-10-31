"""Both cabinet LED chains on a Pi 5, through the kernel's PIO driver.

rpi_ws281x can't reach a Pi 5's pins (they're on the RP1 chip); the kernel
drives WS281x through RP1's PIO block instead (ws2812-pio-rp1). Each chain
is a dtoverlay in config.txt's [pi5] section (docs/pi5-move.md):

    dtoverlay=ws2812-pio,gpio=12,num_leds=23,rgbw,brightness=0,dev_name=leds-buttons
    dtoverlay=ws2812-pio,gpio=13,num_leds=349,brightness=0,dev_name=leds-accent

and shows up as /dev/leds-buttons, /dev/leds-accent. A frame is written in
one go: 4 bytes per LED (R, G, B, W) whatever the strip type; the driver
puts them in wire order (GRB or GRBW). brightness=0 is its pass-through
mode (no gamma, no scaling): jb-lights does its own.

The buttons' data line is inverted on the board (!D5 on the Pixel Shifter;
rpi_ws281x inverts it in software on the Pi 4). The driver has no invert,
so the pin's output is inverted in RP1 itself: OUTOVER in the pin's
GPIO_CTRL register, through /dev/gpiomem0. The driver sets the pin's
function once, when it loads, so this holds until a reboot.

Same face as pwm_chains (get().buttons / .accent, setPixelColor, show).
"""

import mmap
import os
import struct
import threading
from pathlib import Path

from services.jb_lights import accent, buttons

DEV = Path("/dev")
BUTTONS_DEV = "leds-buttons"
ACCENT_DEV = "leds-accent"
GPIOMEM = Path("/dev/gpiomem0")      # RP1 io_bank0 at offset 0
# The whole RP1 GPIO block, as pinctrl maps it: the driver refuses a
# smaller mapping (EINVAL, the first Pi 5 boot, 2026-10-20). Only this
# device: mapping another gpiomem past its own size can hang the board.
GPIOMEM_SIZE = 0x30000

# RP1 io_bank0: GPIOn_STATUS at 8n, GPIOn_CTRL at 8n + 4. OUTOVER (bits
# 13:12): 0 = the peripheral's signal, 1 = inverted, 2 = low, 3 = high.
OUTOVER_SHIFT = 12
OUTOVER_MASK = 0b11 << OUTOVER_SHIFT
OUTOVER_INVERT = 1


def ctrl_offset(gpio: int) -> int:
    return 8 * gpio + 4


def invert_output(gpio: int, path: Path = GPIOMEM) -> tuple[int, int]:
    """Inverts a pin's output in RP1. Returns the CTRL register (before, after)."""
    fd = os.open(path, os.O_RDWR | os.O_SYNC)
    try:
        mem = mmap.mmap(fd, GPIOMEM_SIZE)
        try:
            off = ctrl_offset(gpio)
            before = struct.unpack_from("<I", mem, off)[0]
            after = (before & ~OUTOVER_MASK) | (OUTOVER_INVERT << OUTOVER_SHIFT)
            if after != before:
                struct.pack_into("<I", mem, off, after)
            return before, struct.unpack_from("<I", mem, off)[0]
        finally:
            mem.close()
    finally:
        os.close(fd)


def available(dev: Path = DEV) -> bool:
    """Both chains' devices are there (the [pi5] overlays loaded)."""
    return (dev / BUTTONS_DEV).exists() and (dev / ACCENT_DEV).exists()


def present(dev: Path = DEV) -> set[str]:
    """The chains whose devices are there: "buttons", "accent"."""
    return {name for name, file in (("buttons", BUTTONS_DEV), ("accent", ACCENT_DEV)) if (dev / file).exists()}


class Chain:
    """A PixelStrip-like view of one /dev/leds-* device."""

    # Not parked with pinctrl on close (buttons.close_strip): that would
    # take the pin away from PIO until the next reboot.
    parks_pin = False
    # Each chain is its own device: nothing else writes this one's frames.
    # (On a Pi 4 the buttons share the accent chain's controller, and its
    # thread sends both, so the keypad's show only sets its pixels.)
    own_show = True

    def __init__(self, path: Path, count: int, lock: threading.Lock):
        self.size = count
        self._buf = bytearray(4 * count)
        self._lock = lock
        self._fd = os.open(path, os.O_WRONLY)

    def setPixelColor(self, n, color):
        # rpi_ws281x's Color(): W << 24 | R << 16 | G << 8 | B.
        if 0 <= n < self.size:
            self._buf[4 * n:4 * n + 4] = bytes(((color >> 16) & 255, (color >> 8) & 255, color & 255, (color >> 24) & 255))

    def numPixels(self):
        return self.size

    def show(self):
        with self._lock:
            if self._fd is not None:
                os.pwrite(self._fd, self._buf, 0)

    def close(self):
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None


class Chains:
    """A chain whose device is missing (its overlay not loaded) is None."""

    def __init__(self, dev: Path = DEV, gpiomem: Path = GPIOMEM, log=print):
        lock = threading.Lock()
        have = present(dev)
        self.buttons = Chain(dev / BUTTONS_DEV, buttons.NUM_PIXELS, lock) if "buttons" in have else None
        self.accent = Chain(dev / ACCENT_DEV, accent.NUM_PIXELS, lock) if "accent" in have else None
        if self.buttons:
            before, after = invert_output(buttons.GPIO, gpiomem)
            log(f"jb-lights: GPIO{buttons.GPIO} inverted for the buttons (CTRL {before:#010x} -> {after:#010x})")


_chains = None
_get_lock = threading.Lock()


def get() -> Chains:
    global _chains
    with _get_lock:
        if _chains is None:
            _chains = Chains()
        return _chains
