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
Modules add to every chain over the bus (show/inputs.py), and each chain's
last step is the safety pass (stack.py, docs/contracts/lights.md).

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
import time
import traceback

from services.jb_lights import accent_lights, levelbar, pio_chains, present
from services.jb_lights.audio_feed import AudioFeed
from services.jb_lights.show.inputs import Inputs
from services.jb_lights.themes import buttons_theme1
from shared import hardware

BAR_THEME = 5   # idle colour stripes beside the city (user, 2026-10-19); music picks the rest
STATE_TOPIC = "jukebox/jb-lights/state"
REPORT_S = 2.0


def state(feed, inputs, run) -> dict:
    """What the lights are doing, for admin › Light Show › Now (retained)."""
    snap = feed.last
    lib = feed.director.library
    cues = snap.cues if snap else None
    show = cues.style if cues else None
    return {"v": 1, "look": snap.look if snap else "idle", "chains": sorted(run),
            "show": {"id": show.name, "name": show.title or show.name, "source": show.source} if show else None,
            "tier": cues.tier if cues else None, "idle": lib.settings["idle"], "pin": lib.settings["pin"],
            "inputs": inputs.report(), "notes": lib.notes[:20], "ts": int(time.time())}


def report(feed, inputs, run, stop):
    while not stop.wait(REPORT_S):
        try:
            inputs.publish(STATE_TOPIC, state(feed, inputs, run), retain=True)
        except Exception:           # the report is a courtesy: never take the lights down for it
            traceback.print_exc()


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
    inputs = Inputs().start()        # modules' lights (docs/contracts/lights.md); shared too

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
        workers.append(supervised("levelbar", lambda: levelbar.run(BAR_THEME, stop=stop, feed=feed, inputs=inputs)))
    if "accent" in run:
        workers.append(supervised("accent", lambda: accent_lights.run(stop=stop, feed=feed, inputs=inputs)))
    for w in workers:
        w.start()
    threading.Thread(target=report, args=(feed, inputs, run, stop), name="report", daemon=True).start()
    try:
        if "buttons" in run:
            buttons_theme1.main(feed=feed, inputs=inputs)
        else:
            threading.Event().wait()     # until SIGTERM
    finally:
        stop.set()
        for w in workers:
            w.join(timeout=5)
        feed.stop()
        inputs.stop()


if __name__ == "__main__":
    main()
