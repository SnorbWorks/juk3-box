"""Teaching the cabinet's buttons from jb-input's learn events."""

from services.jb_admin.controls_teach import Teacher
from shared import selection

PICO = "239a:80f4:Jukebox JB Keypad"
ARCADE = "16c0:27db:Arcade Board"


class Clock:
    def __init__(self):
        self.t = 100.0

    def ev(self, code, state="down", device=PICO, after=0.2):
        self.t += after
        return {"device": device, "role": "keypad", "code": code, "state": state, "t": round(self.t * 1000)}


def press(teacher, clock, code, device=PICO, length=0.08, after=0.3):
    teacher.heard(clock.ev(code, "down", device, after), clock.t)
    teacher.heard(clock.ev(code, "up", device, length), clock.t)


def turn(teacher, clock, code, steps, device=PICO):
    for i in range(steps):
        teacher.heard(clock.ev(code, "down", device, 0.3 if i == 0 else 0.004), clock.t)
        teacher.heard(clock.ev(code, "up", device, 0.002), clock.t)
    clock.t += 0.5
    teacher.tick(clock.t)


def small():
    return selection.Format({"kind": "letters", "letters": "AB", "digits": "12"})


def test_the_owners_keypad_learns_the_knob_then_turns_into_a_quick_check():
    t, c = Teacher(small()), Clock()
    assert t.view()["prompt"] == "Turn the knob one click to the right"
    turn(t, c, "KEY_RIGHT", 2)
    assert t.steps_per_click == 2
    turn(t, c, "KEY_LEFT", 2)
    press(t, c, "KEY_ENTER")
    press(t, c, "KEY_ESC")
    assert t.view()["prompt"] == "Press A"
    press(t, c, "KEY_A")
    assert t.quick
    for code in ("KEY_2", "KEY_B", "KEY_1"):          # any order
        press(t, c, code)
    assert t.phase == "try"
    assert t.result() == {"keypads": [PICO], "knob_steps_per_click": 2, "settle_ms": {}, "keymap": {
        "KEY_RIGHT": "knob.cw", "KEY_LEFT": "knob.ccw", "KEY_ENTER": "knob.push", "KEY_ESC": "reset",
        "KEY_A": "key.A", "KEY_2": "key.2", "KEY_B": "key.B", "KEY_1": "key.1"}}


def test_a_board_with_other_codes_is_taught_in_order():
    t, c = Teacher(small(), knob=False), Clock()
    for code in ("BTN_TRIGGER", "BTN_THUMB", "BTN_THUMB2", "BTN_TOP", "BTN_TOP2"):
        press(t, c, code, ARCADE)
    assert not t.quick and t.phase == "try"
    assert t.result()["keymap"] == {"BTN_TRIGGER": "reset", "BTN_THUMB": "key.A", "BTN_THUMB2": "key.B",
                                    "BTN_TOP": "key.1", "BTN_TOP2": "key.2"}
    assert t.result()["keypads"] == [ARCADE]


def test_the_same_button_twice_is_caught():
    t, c = Teacher(small(), knob=False, cancel=False), Clock()
    press(t, c, "BTN_0")
    press(t, c, "BTN_0")
    assert t.message == "That's the same button as A."
    assert t.view()["prompt"] == "Press B"


def test_a_knob_wired_backwards_still_turns_the_right_way():
    t, c = Teacher(small(), cancel=False), Clock()
    turn(t, c, "KEY_LEFT", 1)       # its "right" sends LEFT
    turn(t, c, "KEY_LEFT", 1)
    assert "right again" in t.message
    turn(t, c, "KEY_RIGHT", 1)
    assert t.keymap() == {"KEY_LEFT": "knob.cw", "KEY_RIGHT": "knob.ccw"}


def test_a_bouncing_button_gets_a_longer_settle_time():
    t, c = Teacher(small(), knob=False, cancel=False), Clock()
    t.heard(c.ev("BTN_0", "down"), c.t)
    t.heard(c.ev("BTN_0", "up", after=0.01), c.t)
    t.heard(c.ev("BTN_0", "down", after=0.01), c.t)       # 20 ms later: a bounce
    t.heard(c.ev("BTN_0", "up", after=0.08), c.t)
    assert t.view()["prompt"] == "Press B"                # counted once
    assert t.result()["settle_ms"] == {"BTN_0": 120}
    assert t.bouncy == ["A"] and "bouncy" in t.message


def test_a_button_held_down_is_reported():
    t, c = Teacher(small(), knob=False), Clock()
    t.heard(c.ev("KEY_ESC", "down"), c.t)
    t.tick(c.t + 3.5)
    assert "held down" in t.view()["message"]
    t.heard(c.ev("KEY_ESC", "up", after=3.6), c.t)
    assert t.view()["message"] is None


def test_skip_back_and_try_it():
    t, c = Teacher(small(), knob=False), Clock()
    press(t, c, "KEY_ESC")
    t.skip()                                               # A doesn't work
    t.back()
    assert t.view()["prompt"] == "Press A"
    press(t, c, "BTN_A")
    t.skip()                                               # nor B
    press(t, c, "KEY_1")
    press(t, c, "KEY_2")
    assert t.phase == "try" and t.view()["targets"][2]["state"] == "skipped"
    press(t, c, "BTN_A")
    press(t, c, "KEY_2")
    assert t.trial["picked"] == {"code": "A2", "n": 2}
    press(t, c, "KEY_1")
    assert t.trial["said"] == "A letter comes first."
    press(t, c, "KEY_Q")
    assert t.trial["said"] == "That button wasn't taught."
