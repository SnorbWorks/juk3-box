"""jb-lights service entry point.

Drives the button pixels with buttons-theme1. The level bar joins here once
its BAR_DATA line is reworked off the Pixel Shifter's inverting !D5 output.

Runs as root (rpi_ws281x needs it), via deploy/systemd/jb-lights.service.
systemd stops services with SIGTERM, which would skip cleanup and leave the
buttons latched on their last frame with the pin unparked, so SIGTERM is
turned into a normal exit.
"""

import signal
import sys

from services.jb_lights.themes import buttons_theme1


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    buttons_theme1.main()


if __name__ == "__main__":
    main()
