"""Level bar bring-up / fault-finding patterns (run on the Pi as admin, no root):

  python -m services.jb_lights.bartest white      # whole bar white
  python -m services.jb_lights.bartest strips     # each strip its own color (red, green, blue,
                                                  #   yellow, cyan, magenta, repeating)
  python -m services.jb_lights.bartest chase      # one strip at a time, walking left -> right
  python -m services.jb_lights.bartest orient     # each strip: bottom px red, top px blue;
                                                  #   strip 1 also gets a green middle px
  python -m services.jb_lights.bartest off

Patterns are resent continuously (Ctrl-C to stop) so they survive glitches and
hot-plugged wires. Default brightness 20%; --brightness raises it (0.0-1.0).
The 4A level-bar cap is always applied.

Lessons from bring-up (2026-09-30 / 10-01): the bar is 36 x 5 (see matrix.py);
a bar that stops lighting at a fixed strip means a bad joint right after it;
and the LED board's U1 level-shifter path wouldn't drive the bar even though
it passed every DC test -- Pi GPIO10 wired straight to DIN worked.
"""

import argparse
import time

from services.jb_lights import matrix
from services.jb_lights.ws2812_spi import WS2812SPI

WHITE = (255, 255, 255)
RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)
STRIP_COLORS = [RED, GREEN, BLUE, (255, 170, 0), (0, 255, 255), (255, 0, 255)]


def column(color):
    return [color] * matrix.ROWS


def strips_frame():
    return [px for c in range(matrix.COLS) for px in column(STRIP_COLORS[c % len(STRIP_COLORS)])]


def orient_frame():
    frame = matrix.blank()
    for c in range(matrix.COLS):
        frame[matrix.index(c, 0)] = RED
        frame[matrix.index(c, matrix.ROWS - 1)] = BLUE
    frame[matrix.index(0, matrix.ROWS // 2)] = GREEN
    return frame


def chase_frames():
    for lit in range(matrix.COLS):
        color = STRIP_COLORS[lit % len(STRIP_COLORS)]
        yield [px for c in range(matrix.COLS) for px in column(color if c == lit else matrix.BLACK)]


def hold(bar, frame, seconds=None):
    end = None if seconds is None else time.time() + seconds
    while end is None or time.time() < end:
        bar.show(frame)
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", choices=["white", "strips", "chase", "orient", "off"])
    ap.add_argument("--brightness", type=float, default=0.20)
    ap.add_argument("--step", type=float, default=0.33, help="seconds per strip for chase")
    args = ap.parse_args()

    bar = WS2812SPI(matrix.NUM_PIXELS, brightness=args.brightness, max_amps=matrix.MAX_AMPS)
    try:
        if args.pattern == "off":
            bar.off()
        elif args.pattern == "white":
            hold(bar, [WHITE] * matrix.NUM_PIXELS)
        elif args.pattern == "strips":
            hold(bar, strips_frame())
        elif args.pattern == "orient":
            hold(bar, orient_frame())
        elif args.pattern == "chase":
            while True:
                for frame in chase_frames():
                    hold(bar, frame, args.step)
    except KeyboardInterrupt:
        bar.off()
    finally:
        bar.close()


if __name__ == "__main__":
    main()
