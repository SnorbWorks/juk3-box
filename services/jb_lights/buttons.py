"""Button pixel chain: 23 SK6812 RGBW pixels behind the keypad buttons.

Data goes out on GPIO12 (PWM0, rpi_ws281x channel 0, DMA 10), which needs
root. Chain order, as wired:

    0-9    letters, backwards: K J H G F E D C B A
    10     SINGLE PLAY indicator   } behind a colored filter: use white
    11     ALBUM PLAY indicator    } (blue is blocked)
    12-21  digits, forwards: 1 2 3 4 5 6 7 8 9 0
    22     RESET

The power board (revE) takes BTN_DATA from the Pixel Shifter's !D5 output,
which inverts the signal, so the strip is opened with invert=True. With
nothing driving the pin it falls low and the buttons see a stuck-high line
(and latch junk), so close() parks GPIO12 as an output driven HIGH.
"""

import subprocess

from rpi_ws281x import Color

NUM_PIXELS = 23
GPIO = 12
LETTERS = "ABCDEFGHJK"   # no I
DIGITS = "1234567890"
SINGLE, ALBUM, RESET = 10, 11, 22


def index(key: str) -> int:
    """Pixel index for a selection key ("A".."K" or "0".."9")."""
    if key in LETTERS:
        return 9 - LETTERS.index(key)
    return 12 + DIGITS.index(key)


def open_strip():
    """The buttons' chain, from the controller it shares with the accent
    chain (pwm_chains: both PWM channels share one DMA engine; pio_chains
    on a Pi 5)."""
    from services.jb_lights import chains   # here, not at the top: the drivers import this module
    return chains.get().buttons


def close_strip(strip) -> None:
    """All off, then park the pin HIGH so the inverted line rests low."""
    for i in range(NUM_PIXELS):
        strip.setPixelColor(i, Color(0, 0, 0, 0))
    strip.show()
    strip.show()
    if getattr(strip, "parks_pin", True):     # not on a Pi 5: the pin belongs to PIO
        subprocess.run(["pinctrl", "set", str(GPIO), "op", "dh"], check=False)
