"""jb-lights service entry point.

Drives the button pixels with buttons-theme1 (main thread) and the level bar
with levelbar.run() (its own thread; idle white, volume fill while the knob
turns).

Runs as root (rpi_ws281x needs it), via deploy/systemd/jb-lights.service.
systemd stops services with SIGTERM, which would skip cleanup and leave the
lights latched on their last frame with the button pin unparked, so SIGTERM
is turned into a normal exit. If the bar thread dies, the whole service
exits non-zero so systemd restarts it rather than running half-lit.
"""

import os
import signal
import sys
import threading
import traceback

from services.jb_lights import levelbar
from services.jb_lights.themes import buttons_theme1

BAR_THEME = 0   # idle white; themes 1-3 come with the audio mood analyzer


def main():
    failed = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1 if failed.is_set() else 0))

    stop = threading.Event()

    def bar():
        try:
            levelbar.run(BAR_THEME, stop=stop)
        except Exception:
            traceback.print_exc()
            failed.set()
            os.kill(os.getpid(), signal.SIGTERM)

    bar_thread = threading.Thread(target=bar, name="levelbar")
    bar_thread.start()
    try:
        buttons_theme1.main()
    finally:
        stop.set()
        bar_thread.join(timeout=5)


if __name__ == "__main__":
    main()
