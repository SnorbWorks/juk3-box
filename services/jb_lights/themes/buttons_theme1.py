"""buttons-theme1: selection feedback on the button pixels.

The user's design, tested 2026-10-10:
  - idle: every button white at 70%
  - letter pressed: that letter green, and RESET red (the selection can be
    cancelled); another letter moves the green
  - digit after a letter: the digit goes green too, then a rainbow comet runs
    A..K, 1..0 twice, then back to all white
  - digit with no letter first: that digit blinks red (not a valid start)
  - RESET while a letter is held: cancel, back to all white
SINGLE/ALBUM stay white and sit out the chase. Presses during the chase or
the blink are dropped.

Listens to jb-input's jukebox/jb-input/key and jukebox/jb-input/gesture:

    sudo venv/bin/python -m services.jb_lights.themes.buttons_theme1
"""

import argparse
import colorsys
import json
import queue
import time

import paho.mqtt.client as mqtt
from rpi_ws281x import Color

from services.jb_lights import buttons

WHITE = Color(0, 0, 0, int(255 * 0.70))
GREEN = Color(0, 200, 0, 0)
RED = Color(200, 0, 0, 0)
OFF = Color(0, 0, 0, 0)
CHASE = [buttons.index(k) for k in buttons.LETTERS + buttons.DIGITS]
CHASE_CYCLES = 2
COMET_TAIL = 5
CHASE_STEP_S = 0.035
CONFIRM_HOLD_S = 0.6      # both picks green before the chase starts
BLINKS = 3


class Theme1:
    def __init__(self, strip):
        self.strip = strip
        self.letter = None

    def paint(self, colors=None):
        colors = colors or {}
        for i in range(buttons.NUM_PIXELS):
            self.strip.setPixelColor(i, colors.get(i, WHITE))
        self.strip.show()

    def blink_red(self, i):
        for _ in range(BLINKS):
            self.paint({i: RED})
            time.sleep(0.18)
            self.paint({i: OFF})
            time.sleep(0.12)

    def comet(self):
        n = len(CHASE)
        total = CHASE_CYCLES * n
        for f in range(total + COMET_TAIL):
            frame = {i: OFF for i in range(buttons.NUM_PIXELS)}
            for k in range(COMET_TAIL):
                pos = f - k
                if 0 <= pos < total:
                    r, g, b = colorsys.hsv_to_rgb((pos / n) % 1.0, 1, (1 - k / COMET_TAIL) ** 1.5)
                    frame[CHASE[pos % n]] = Color(int(r * 255), int(g * 255), int(b * 255), 0)
            self.paint(frame)
            time.sleep(CHASE_STEP_S)

    def on_key(self, key, kind):
        """Returns True when an animation ran (queued presses get dropped)."""
        if kind == "letter":
            self.letter = key
            self.paint({buttons.index(key): GREEN, buttons.RESET: RED})
            return False
        if self.letter is None:
            self.blink_red(buttons.index(key))
            self.paint()
            return True
        self.paint({buttons.index(self.letter): GREEN, buttons.index(key): GREEN})
        time.sleep(CONFIRM_HOLD_S)
        self.comet()
        self.letter = None
        self.paint()
        return True

    def on_reset(self):
        if self.letter is not None:
            self.letter = None
            self.paint()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    args = ap.parse_args()

    events = queue.Queue()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: (c.subscribe("jukebox/jb-input/key"),
                                                     c.subscribe("jukebox/jb-input/gesture"))
    client.on_message = lambda c, u, msg: events.put((msg.topic, json.loads(msg.payload)))
    client.connect(args.host, 1883)
    client.loop_start()

    strip = buttons.open_strip()
    theme = Theme1(strip)
    theme.paint()
    try:
        while True:
            topic, p = events.get()
            if topic.endswith("/key"):
                if theme.on_key(p["key"], p["kind"]):
                    while not events.empty():
                        events.get_nowait()
            elif p.get("control") == "reset" and p.get("gesture") == "click":
                theme.on_reset()
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        buttons.close_strip(strip)


if __name__ == "__main__":
    main()
