"""buttons-theme1: selection feedback on the button pixels.

The user's design, tested 2026-10-10:
  - idle: every button white at 70%
  - letter waiting: that letter green, and RESET red (the selection can be
    cancelled); another letter moves the green. On a numbers-only jukebox
    the digits typed so far are green the same way (added 2026-10-22)
  - selection made: letter and digit blink green together, then a rainbow
    comet runs A..K, 1..0 twice, then back to all white
  - digit with no letter first: that digit blinks red, lingering (not a
    valid start)
  - cancelled (RESET): RESET blinks red to acknowledge, with the letter still
    green, then all white
  - timed out: straight back to all white
  - a pick that didn't play (jb-core's pick doctor): the whole keypad
    swells amber twice, slowly -- the screen says why (added 2026-10-18)
(blink changes 2026-10-11)
SINGLE/ALBUM stay white and sit out the chase.

The lights show jb-core's selection state rather than reading keys
themselves, so they always agree with what jb-core will actually play:
jukebox/jb-core/entry (retained: idle / partial + what's typed, and why it
went idle), /selection (the slot picked) and /rejected (a digit with no letter).
After an animation, anything that arrived meanwhile is folded into jb-core's
latest entry state, not replayed.

    sudo venv/bin/python -m services.jb_lights.themes.buttons_theme1
"""

import argparse
import colorsys
import json
import math
import queue
import time

import paho.mqtt.client as mqtt
from rpi_ws281x import Color

from services.jb_lights import buttons
from services.jb_lights.themes.buttons_music import ButtonsMusic

TOPICS = ("jukebox/jb-core/entry", "jukebox/jb-core/selection", "jukebox/jb-core/rejected",
          "jukebox/jb-core/skipped", "jukebox/jb-core/power", "jukebox/jb-input/raw",
          "jukebox/jb-core/pick-problem")

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
PROBLEM_SWELLS = 2        # a pick that didn't play: slow amber swells, unlike any other feedback
PROBLEM_SWELL_S = 0.9
PROBLEM_STEPS = 24
MUSIC_FPS = 30            # the keypad's part of the music show


class Theme1:
    def __init__(self, strip):
        self.strip = strip
        self.reset_held = False

    def paint(self, colors=None, show=True):
        colors = colors or {}
        for i in range(buttons.NUM_PIXELS):
            self.strip.setPixelColor(i, colors.get(i, WHITE))
        if show:
            self.strip.show()

    @staticmethod
    def typed_keys(typed):
        """Green for each key typed so far (a letter, or a numbers-only
        jukebox's digits)."""
        return {buttons.index(k): GREEN for k in typed or "" if buttons.has(k)}

    def show_entry(self, typed):
        """Resting look for jb-core's entry state: what's typed so far, or
        idle; RESET red while something can be cancelled or it's held down."""
        colors = self.typed_keys(typed)
        if typed:
            colors[buttons.RESET] = RED
        if self.reset_held:
            colors[buttons.RESET] = RED
        self.paint(colors)

    def reset_blink(self, typed):
        """RESET blinks red twice: the song was cancelled, or the power menu
        opened (holding Reset)."""
        base = self.typed_keys(typed)
        self.blink(base, {buttons.RESET: RED}, {buttons.RESET: OFF}, RESET_BLINKS, RESET_ON_S, RESET_OFF_S)

    def blink(self, base, on, off, times, on_s, off_s):
        """Alternate base+on and base+off frames."""
        for _ in range(times):
            self.paint({**base, **on})
            time.sleep(on_s)
            self.paint({**base, **off})
            time.sleep(off_s)

    def rejected(self, key):
        if not buttons.has(key):
            return
        i = buttons.index(key)
        self.blink({}, {i: RED}, {i: OFF}, REJECT_BLINKS, REJECT_ON_S, REJECT_OFF_S)

    def cancelled(self, typed):
        base = self.typed_keys(typed)
        self.blink(base, {buttons.RESET: RED}, {buttons.RESET: OFF},
                   CANCEL_BLINKS, CANCEL_ON_S, CANCEL_OFF_S)

    def selected(self, slot):
        picks = [buttons.index(k) for k in dict.fromkeys(slot) if buttons.has(k)]
        self.blink({}, {i: GREEN for i in picks}, {i: OFF for i in picks},
                   CONFIRM_BLINKS, CONFIRM_ON_S, CONFIRM_OFF_S)
        self.comet()

    def pick_problem(self):
        """Every key swells amber and fades, twice."""
        keys = range(buttons.NUM_PIXELS)
        for _ in range(PROBLEM_SWELLS):
            for s in range(PROBLEM_STEPS + 1):
                v = math.sin(math.pi * s / PROBLEM_STEPS)
                self.paint({i: Color(int(255 * v), int(80 * v), 0, 0) for i in keys})
                time.sleep(PROBLEM_SWELL_S / PROBLEM_STEPS)

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


def main(feed=None):
    """Runs until Ctrl-C. With a feed (AudioFeed), the keypad joins the music
    show (buttons_music) whenever nothing is waiting on it."""
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
    typed = None    # what's entered so far (jb-core's retained entry topic)
    menu = False    # the power menu is open (jukebox/jb-core/power)
    music = ButtonsMusic() if feed else None
    showing_music = False
    last = time.monotonic()

    def music_frame():
        """Between events: the keypad's part of the light show, when it's
        resting. On a Pi 4 the accent chain renders the shared controller ~40
        times a second, so this only sets the pixels; on a Pi 5 the keypad is
        its own device and sends its own frames (pio_chains.Chain.own_show)."""
        nonlocal showing_music, last
        now = time.monotonic()
        dt, last = min(0.1, now - last), now
        if not music or typed or theme.reset_held or menu:
            return
        colours = music.frame(dt, feed.snapshot(now))
        if colours is None:
            if showing_music:
                theme.paint()                           # the show is over: plain white
                showing_music = False
            return
        showing_music = True
        theme.paint({i: Color(*c) for i, c in colours.items()}, show=getattr(theme.strip, "own_show", False))

    def interacted():
        nonlocal showing_music
        if showing_music and music:
            music.resume()                              # fade back in afterwards
        showing_music = False

    def follow_state(topic, p):
        """State-only updates (no animation): returns True if handled."""
        nonlocal typed
        if topic.endswith("/raw"):
            if p.get("control") == "reset":
                theme.reset_held = p.get("state") == "down"
            return True
        if topic.endswith("/entry"):
            # "typed" since the selection formats (Oct 21); "letter" before.
            typed = (p.get("typed") or p.get("letter")) if p.get("state") == "partial" else None
            return True
        return False

    try:
        while True:
            try:
                topic, p = events.get(timeout=1 / MUSIC_FPS if music else None)
            except queue.Empty:
                music_frame()
                continue
            if topic.endswith("/raw") and p.get("control") != "reset":
                continue                                # only Reset's raw state matters here
            interacted()
            if topic.endswith("/entry"):
                was = typed
                follow_state(topic, p)
                if not (typed is None and was and p.get("reason") == "cancelled"):
                    theme.show_entry(typed)
                    continue
                theme.cancelled(was)
            elif topic.endswith("/raw"):
                follow_state(topic, p)
                theme.show_entry(typed)
                continue
            elif topic.endswith("/power"):
                opened = p.get("state") == "menu" and not menu
                menu = p.get("state") == "menu"
                if not opened:
                    continue
                theme.reset_blink(typed)
            elif topic.endswith("/skipped"):
                theme.reset_blink(typed)
            elif topic.endswith("/selection"):
                theme.selected(p["slot"])
            elif topic.endswith("/rejected"):
                theme.rejected(p["key"])
            elif topic.endswith("/pick-problem"):
                if p.get("problem") == "retrying":
                    continue
                theme.pick_problem()
            # Catch up on whatever came in during the animation: keep the
            # state (entry, Reset held, menu), not the animations, then show it.
            while not events.empty():
                t, q = events.get_nowait()
                if not follow_state(t, q) and t.endswith("/power"):
                    menu = q.get("state") == "menu"
            theme.show_entry(typed)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        buttons.close_strip(strip)


if __name__ == "__main__":
    main()
