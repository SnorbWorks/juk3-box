"""jb-lights service entry point.

Drives every LED chain:
  - buttons      buttons-theme1, in the main thread (reacts to jb-core)
  - level bar    levelbar.run() in its own thread (idle stripes, volume fill)
  - accent chain accent_lights.run() in its own thread: the upper panel and
                 footlights, accent-city at idle
While music plays, all of them put on one light show from jb-analyzer's
stream through the shared AudioFeed: its director picks a style for the
song and cues each part (show/, docs/Light Show Styles.md).
The buttons and the accent chain share one rpi_ws281x controller on a
Pi 4 (pwm_chains) and use the kernel's PIO driver on a Pi 5 (pio_chains);
the level bar is on SPI on both.

Lights are optional (the setup wizard's Lights step): only the chains
lights.json says are wired in run (present.py), and with none the service
stays up doing nothing. On a Pi 5 a chain also needs its device (its PIO
overlay); without it that chain is skipped, not the service restarted.

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

from services.jb_lights import accent_lights, levelbar, pio_chains, present
from services.jb_lights.audio_feed import AudioFeed
from services.jb_lights.themes import buttons_theme1
from shared import hardware

BAR_THEME = 5   # idle colour stripes beside the city (user, 2026-10-19); music picks the rest


def chains_to_run(wanted: dict[str, bool], pio: set[str] | None) -> tuple[set[str], list[str]]:
    """The chains to drive, and why any wanted one isn't. `pio` is the Pi 5's
    chain devices that are there (pio_chains.present()), None on a Pi 4."""
    run, notes = set(), []
    for name in present.CHAINS:
        if not wanted.get(name):
            continue
        if pio is not None and name != "levelbar" and name not in pio:
            notes.append(f"no /dev/leds-{name} (its ws2812-pio overlay, docs/pi5-move.md): {name} stays off")
            continue
        run.add(name)
    return run, notes


def main():
    board = hardware.board()
    wanted = present.read()
    run, notes = chains_to_run(wanted, pio_chains.present() if board["family"] == "pi5" else None)
    for note in notes:
        print(f"jb-lights: {board['model']}: {note}", flush=True)
    if not run:
        # No lights (or none whose device is there): stay up and idle rather
        # than fail and be restarted over and over.
        print(f"jb-lights: no lights to drive ({', '.join(n for n in present.CHAINS if wanted[n]) or 'none wired in'})", flush=True)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        threading.Event().wait()
        return

    failed = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1 if failed.is_set() else 0))

    stop = threading.Event()
    feed = AudioFeed().start()       # the music, from jb-analyzer; shared so the chains agree

    def supervised(name, target):
        def body():
            try:
                target()
            except Exception:
                traceback.print_exc()
                failed.set()
                os.kill(os.getpid(), signal.SIGTERM)
        return threading.Thread(target=body, name=name)

    workers = []
    if "levelbar" in run:
        workers.append(supervised("levelbar", lambda: levelbar.run(BAR_THEME, stop=stop, feed=feed)))
    if "accent" in run:
        workers.append(supervised("accent", lambda: accent_lights.run(stop=stop, feed=feed)))
    for w in workers:
        w.start()
    try:
        if "buttons" in run:
            buttons_theme1.main(feed=feed)
        else:
            threading.Event().wait()     # until SIGTERM
    finally:
        stop.set()
        for w in workers:
            w.join(timeout=5)
        feed.stop()


if __name__ == "__main__":
    main()
