"""buttons-theme1: selection feedback on the button pixels.

The user's design, tested 2026-10-10:
  - idle: every button white at 70%
  - letter waiting: that letter green, and RESET red (the selection can be
    cancelled); another letter moves the green
  - selection made: letter and digit blink green together, then a rainbow
    comet runs A..K, 1..0 twice, then back to all white
  - digit with no letter first: that digit blinks red, lingering (not a
    valid start)
  - cancelled (RESET): RESET blinks red to acknowledge, with the letter still
    green, then all white
  - timed out: straight back to all white
(blink changes 2026-10-11)
SINGLE/ALBUM stay white and sit out the chase.

The lights show jb-core's selection state rather than reading keys
themselves, so they always agree with what jb-core will actually play:
jukebox/jb-core/entry (retained: idle / partial + letter, and why it went
idle), /selection (the slot picked) and /rejected (a digit with no letter).
After an animation, anything that arrived meanwhile is folded into jb-core's
latest entry state, not replayed.

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

TOPICS = ("jukebox/jb-core/entry", "jukebox/jb-core/selection", "jukebox/jb-core/rejected",
          "jukebox/jb-core/skipped", "jukebox/jb-core/power", "jukebox/jb-input/raw")

WHITE = Color(0, 0, 0, int(255 * 0.70))
GREEN = Color(0, 200, 0, 0)
RED = Color(200, 0, 0, 0)
OFF = Color(0, 0, 0, 0)
CHASE = [buttons.index(k) for k in buttons.LETTERS + buttons.DIGITS]
CHASE_CYCLES = 2
COMET_TAIL = 5
CHASE_STEP_S = 0.035
CONFIRM_BLINKS = 3        # letter + digit blink green together before the chase
CONFIRM_ON_S, CONFIRM_OFF_S = 0.22, 0.15
REJECT_BLINKS = 5         # a digit with no letter: slow, lingering red blinks
REJECT_ON_S, REJECT_OFF_S = 0.30, 0.20
CANCEL_BLINKS = 3         # RESET acknowledges the cancel
RESET_BLINKS = 2          # holding Reset: the song cancelled, and again when the power menu opens
RESET_ON_S, RESET_OFF_S = 0.18, 0.14
CANCEL_ON_S, CANCEL_OFF_S = 0.15, 0.12


class Theme1:
    def __init__(self, strip):
        self.strip = strip
        self.reset_held = False

    def paint(self, colors=None):
        colors = colors or {}
        for i in range(buttons.NUM_PIXELS):
            self.strip.setPixelColor(i, colors.get(i, WHITE))
        self.strip.show()

    def show_entry(self, letter):
        """Resting look for jb-core's entry state: a waiting letter, or idle;
        RESET red while it's held down."""
        colors = {buttons.index(letter): GREEN, buttons.RESET: RED} if letter else {}
        if self.reset_held:
            colors[buttons.RESET] = RED
        self.paint(colors)

    def reset_blink(self, letter):
        """RESET blinks red twice: the song was cancelled, or the power menu
        opened (holding Reset)."""
        base = {buttons.index(letter): GREEN} if letter else {}
        self.blink(base, {buttons.RESET: RED}, {buttons.RESET: OFF}, RESET_BLINKS, RESET_ON_S, RESET_OFF_S)

    def blink(self, base, on, off, times, on_s, off_s):
        """Alternate base+on and base+off frames."""
        for _ in range(times):
            self.paint({**base, **on})
            time.sleep(on_s)
            self.paint({**base, **off})
            time.sleep(off_s)

    def rejected(self, key):
        i = buttons.index(key)
        self.blink({}, {i: RED}, {i: OFF}, REJECT_BLINKS, REJECT_ON_S, REJECT_OFF_S)

    def cancelled(self, letter):
        base = {buttons.index(letter): GREEN} if letter else {}
        self.blink(base, {buttons.RESET: RED}, {buttons.RESET: OFF},
                   CANCEL_BLINKS, CANCEL_ON_S, CANCEL_OFF_S)

    def selected(self, slot):
        picks = (buttons.index(slot[0]), buttons.index(slot[1]))
        self.blink({}, {i: GREEN for i in picks}, {i: OFF for i in picks},
                   CONFIRM_BLINKS, CONFIRM_ON_S, CONFIRM_OFF_S)
        self.comet()

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    args = ap.parse_args()

    events = queue.Queue()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: [c.subscribe(t) for t in TOPICS]
    client.on_message = lambda c, u, msg: events.put((msg.topic, json.loads(msg.payload)))
    client.connect(args.host, 1883)
    client.loop_start()

    strip = buttons.open_strip()
    theme = Theme1(strip)
    theme.paint()
    letter = None   # jb-core's waiting letter, from the retained entry topic
    menu = False    # the power menu is open (jukebox/jb-core/power)

    def follow_state(topic, p):
        """State-only updates (no animation): returns True if handled."""
        nonlocal letter
        if topic.endswith("/raw"):
            if p.get("control") == "reset":
                theme.reset_held = p.get("state") == "down"
            return True
        if topic.endswith("/entry"):
            letter = p.get("letter") if p.get("state") == "partial" else None
            return True
        return False

    try:
        while True:
            topic, p = events.get()
            if topic.endswith("/entry"):
                was = letter
                follow_state(topic, p)
                if not (letter is None and was and p.get("reason") == "cancelled"):
                    theme.show_entry(letter)
                    continue
                theme.cancelled(was)
            elif topic.endswith("/raw"):
                follow_state(topic, p)
                theme.show_entry(letter)
                continue
            elif topic.endswith("/power"):
                opened = p.get("state") == "menu" and not menu
                menu = p.get("state") == "menu"
                if not opened:
                    continue
                theme.reset_blink(letter)
            elif topic.endswith("/skipped"):
                theme.reset_blink(letter)
            elif topic.endswith("/selection"):
                theme.selected(p["slot"])
            elif topic.endswith("/rejected"):
                theme.rejected(p["key"])
            # Catch up on whatever came in during the animation: keep the
            # state (entry, Reset held, menu), not the animations, then show it.
            while not events.empty():
                t, q = events.get_nowait()
                if not follow_state(t, q) and t.endswith("/power"):
                    menu = q.get("state") == "menu"
            theme.show_entry(letter)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        buttons.close_strip(strip)


if __name__ == "__main__":
    main()
