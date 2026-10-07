# Pico keypad: scan the matrix, Reset and the encoder; report them as a plain
# USB keyboard. No gesture timing, no selection logic, no knowledge of what a
# key means -- all of that lives in jb-input on the Pi.
import time

import digitalio
import keypad
import rotaryio
import usb_hid
from adafruit_hid.keyboard import Keyboard

import config

matrix = keypad.KeyMatrix(config.ROW_PINS, config.COL_PINS, interval=config.SCAN_INTERVAL_S)
knob_btn = keypad.Keys((config.ENC_SW,), value_when_pressed=False, pull=True)
encoder = rotaryio.IncrementalEncoder(config.ENC_A, config.ENC_B, divisor=config.ENCODER_DIVISOR)

SOURCES = (
    (matrix, config.KEY_CODES, config.KEY_NAMES),
    (knob_btn, (config.ENC_PUSH_CODE,), ("KNOB",)),
)


class ResetButton:
    """Normally closed switch to GND: LOW at rest, HIGH when pressed.

    Not a keypad.Keys: with value_when_pressed=True, keypad's pull=True
    enables a pull-DOWN, so the opened switch never reads HIGH and presses
    are never seen (confirmed on the bench). This needs a pull-UP with
    "high = pressed", so it's read directly with its own debounce.
    """

    def __init__(self, pin, pressed_value, debounce_s):
        self.io = digitalio.DigitalInOut(pin)
        self.io.switch_to_input(pull=digitalio.Pull.UP)
        self.pressed_value = pressed_value
        self.debounce_s = debounce_s
        self.state = self.io.value == pressed_value  # debounced pressed state
        self.candidate, self.since = self.state, time.monotonic()

    def poll(self):
        """Returns True (pressed) / False (released) on a debounced change, else None."""
        now_pressed = self.io.value == self.pressed_value
        now = time.monotonic()
        if now_pressed != self.candidate:
            self.candidate, self.since = now_pressed, now
        elif now_pressed != self.state and now - self.since >= self.debounce_s:
            self.state = now_pressed
            return now_pressed
        return None


reset_btn = ResetButton(config.RESET_PIN, config.RESET_PRESSED_VALUE, config.SCAN_INTERVAL_S)

keyboard = None
last_position = encoder.position
pending_steps = 0
event = keypad.Event()


def log(*args):
    if config.BENCH_MODE:
        print(*args)


def connect():
    global keyboard
    while keyboard is None:
        try:
            keyboard = Keyboard(usb_hid.devices)
        except OSError:  # USB not ready yet (e.g. the Pi is still booting)
            time.sleep(0.1)


def release_all():
    try:
        keyboard.release_all()
    except OSError:
        pass


def send(action, code):
    """Press/release/tap with recovery: on a USB error, release everything,
    pause, and carry on -- the loop must survive the Pi rebooting."""
    try:
        if action == "press":
            keyboard.press(code)
        elif action == "release":
            keyboard.release(code)
        else:
            keyboard.press(code)
            keyboard.release(code)
    except OSError:
        release_all()
        time.sleep(0.1)


connect()
log("JB Keypad ready (bench mode)" if config.BENCH_MODE else "JB Keypad ready")

while True:
    # 1. Drain every pending key event.
    for source, codes, names in SOURCES:
        if source.events.overflowed:
            # A key may have been left stuck down -- clear everything.
            source.events.clear()
            source.reset()
            release_all()
            log("event queue overflow -> released all")
        while source.events.get_into(event):
            code = codes[event.key_number]
            send("press" if event.pressed else "release", code)
            log(names[event.key_number], "down" if event.pressed else "up")

    change = reset_btn.poll()
    if change is not None:
        send("press" if change else "release", config.RESET_CODE)
        log("RESET", "down" if change else "up")

    # 2. Encoder: one tap per detent, capped per loop; the rest carries over.
    position = encoder.position
    delta = position - last_position
    last_position = position
    pending_steps += -delta if config.ENCODER_INVERT else delta
    taps = max(-config.MAX_TAPS_PER_LOOP, min(config.MAX_TAPS_PER_LOOP, pending_steps))
    for _ in range(abs(taps)):
        send("tap", config.ENC_CW_CODE if taps > 0 else config.ENC_CCW_CODE)
    if taps:
        log("KNOB", "cw" if taps > 0 else "ccw", abs(taps))
    pending_steps -= taps

    time.sleep(0.001)
