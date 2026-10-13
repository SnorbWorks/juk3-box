"""jb-lights service entry point.

Drives every LED chain:
  - buttons      buttons-theme1, in the main thread (reacts to jb-core)
  - level bar    levelbar.run() in its own thread (idle white, volume fill)
  - accent chain accent_lights.run() in its own thread: the upper panel and
                 footlights, accent-city at idle
While music plays both follow jb-analyzer's stream through one shared
AudioFeed (EQ or ribbon by mood, a flash on the drop).
The buttons and the accent chain share one rpi_ws281x controller
(pwm_chains); the level bar is on SPI.

Runs as root (rpi_ws281x needs it), via deploy/systemd/jb-lights.service.
systemd stops services with SIGTERM, which would skip cleanup and leave the
lights latched on their last frame with the button pin unparked, so SIGTERM
is turned into a normal exit. If a worker thread dies, the whole service
exits non-zero so systemd restarts it rather than running half-lit.
"""

import os
import signal
import sys
import threading
import traceback

from services.jb_lights import accent_lights, levelbar
from services.jb_lights.audio_feed import AudioFeed
from services.jb_lights.themes import buttons_theme1

BAR_THEME = 0   # idle white; while music plays the analyzer's mood picks the look


def main():
    failed = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1 if failed.is_set() else 0))

    stop = threading.Event()
    feed = AudioFeed().start()       # the music, from jb-analyzer; shared so both chains agree

    def supervised(name, target):
        def body():
            try:
                target()
            except Exception:
                traceback.print_exc()
                failed.set()
                os.kill(os.getpid(), signal.SIGTERM)
        return threading.Thread(target=body, name=name)

    workers = [
        supervised("levelbar", lambda: levelbar.run(BAR_THEME, stop=stop, feed=feed)),
        supervised("accent", lambda: accent_lights.run(stop=stop, feed=feed)),
    ]
    for w in workers:
        w.start()
    try:
        buttons_theme1.main()
    finally:
        stop.set()
        for w in workers:
            w.join(timeout=5)
        feed.stop()


if __name__ == "__main__":
    main()
