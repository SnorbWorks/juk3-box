"""End to end through the pure pipeline, plus the publisher's message shapes."""

from services.jb_input.config import Settings
from services.jb_input.events import Action, ControlEvent, FaultChange, Gesture, KeyPress, RawEvent
from services.jb_input.pipeline import Pipeline
from services.jb_input.publisher import message_for


def of(events, kind):
    return [e for e in events if isinstance(e, kind)]


def press(p, code, t, length=0.05):
    return p.feed(RawEvent(code, 1, t)) + p.feed(RawEvent(code, 0, t + length))


def test_selection_keys_publish_instantly_on_down_and_ignore_up():
    p = Pipeline(Settings())
    out = p.feed(RawEvent("KEY_B", 1, 1.0))
    assert of(out, KeyPress) == [KeyPress("B", "letter", 1.0)]
    assert of(p.feed(RawEvent("KEY_B", 0, 1.1)), KeyPress) == []
    assert of(p.feed(RawEvent("KEY_7", 1, 2.0)), KeyPress) == [KeyPress("7", "digit", 2.0)]


def test_defaults_knob_click_and_reset_click_with_no_actions():
    p = Pipeline(Settings())
    out = press(p, "KEY_ENTER", 1.0) + press(p, "KEY_ESC", 2.0)
    assert of(out, Gesture) == [Gesture("knob", "click", 1.05), Gesture("reset", "click", 2.05)]
    assert of(out, Action) == []


def test_defaults_knob_turn_with_no_action():
    p = Pipeline(Settings())
    for i in range(3):
        press(p, "KEY_RIGHT", 1.0 + i * 0.005, length=0.001)
    out = p.tick(1.05)
    assert of(out, Gesture) == [Gesture("knob", "turn", 1.05, 3)]
    assert of(out, Action) == []


def test_defaults_long_knob_press_is_just_a_click():
    p = Pipeline(Settings())
    out = p.feed(RawEvent("KEY_ENTER", 1, 0.0))
    t = 0.0
    while t < 2.0:
        out += p.tick(t)
        t += 0.01
    out += p.feed(RawEvent("KEY_ENTER", 0, 2.0))
    assert [g.gesture for g in of(out, Gesture)] == ["click"]


def test_configured_actions_and_gestures_are_honoured():
    s = Settings(gestures={"knob": ["hold"], "reset": []}, actions={("knob", "hold"): "menu.open"})
    p = Pipeline(s)
    out = p.feed(RawEvent("KEY_ENTER", 1, 0.0)) + p.tick(1.1)
    assert of(out, Action) == [Action("menu.open", None, "knob", "hold", 1.1)]


def test_stuck_reset_at_startup_raises_fault_and_reconnect_clears_it():
    p = Pipeline(Settings())
    assert p.start_held(["KEY_ESC"], 0.0) == [FaultChange("reset_stuck", True, 0.0)]
    assert p.faults == {"reset_stuck"}
    assert p.reset_state(5.0) == [FaultChange("reset_stuck", False, 5.0)]
    assert p.faults == set()


def test_message_shapes():
    assert message_for(KeyPress("B", "letter", 1.0), False) == (
        "jukebox/jb-input/key", {"key": "B", "kind": "letter"})
    assert message_for(Gesture("knob", "turn", 2.0, 3), False) == (
        "jukebox/jb-input/gesture", {"control": "knob", "gesture": "turn", "steps": 3})
    assert message_for(Gesture("knob", "click", 2.0), False) == (
        "jukebox/jb-input/gesture", {"control": "knob", "gesture": "click"})
    assert message_for(Action("x.y", 3, "knob", "turn", 2.0), False) == (
        "jukebox/jb-input/action", {"action": "x.y", "value": 3, "control": "knob", "gesture": "turn"})
    assert message_for(ControlEvent("key.B", "down", 1.0), False) is None
    assert message_for(ControlEvent("reset", "down", 1.0), False) == (   # Reset: always (the lights)
        "jukebox/jb-input/raw", {"control": "reset", "state": "down"})
    assert message_for(ControlEvent("key.B", "down", 1.0), True) == (
        "jukebox/jb-input/raw", {"control": "key.B", "state": "down"})


def test_a_bouncing_key_counts_once():
    p = Pipeline(Settings())   # settle 40 ms
    out = []
    for i, v in enumerate([1, 0, 1, 0, 1]):          # chatter over 20 ms, then held
        out += p.feed(RawEvent("KEY_B", v, 1.0 + i * 0.005))
    out += p.tick(1.1) + p.feed(RawEvent("KEY_B", 0, 1.3)) + p.feed(RawEvent("KEY_B", 1, 1.6))
    assert of(out, KeyPress) == [KeyPress("B", "letter", 1.0), KeyPress("B", "letter", 1.6)]


def test_a_quick_tap_released_while_settling_is_caught_up():
    p = Pipeline(Settings())
    p.feed(RawEvent("KEY_ESC", 1, 1.0))
    assert p.feed(RawEvent("KEY_ESC", 0, 1.02)) == []          # inside the settle time
    assert of(p.tick(1.05), Gesture) == [Gesture("reset", "click", 1.05)]


def test_settle_time_per_key():
    s = Settings()
    s.settle_by_code = {"KEY_A": 150}
    p = Pipeline(s)
    out = press(p, "KEY_A", 1.0, length=0.2) + press(p, "KEY_A", 1.4)
    assert len(of(out, KeyPress)) == 2   # 200 ms after letting go: a person
    out = press(p, "KEY_A", 2.0, length=0.01) + p.tick(2.16) + press(p, "KEY_A", 2.2) + p.tick(2.5)
    assert len(of(out, KeyPress)) == 1   # 40 ms after: still bouncing, for this key


def test_knob_steps_are_not_settled():
    p = Pipeline(Settings())
    for i in range(4):
        press(p, "KEY_RIGHT", 1.0 + i * 0.002, length=0.001)
    assert of(p.tick(1.05), Gesture) == [Gesture("knob", "turn", 1.05, 4)]


def test_keyboard_volume_backspace_and_calibration():
    s = Settings()
    s.knob_steps_per_click = 2          # learned for the keypad's knob
    kb = Pipeline(s, "keyboard")
    out = press(kb, "KEY_DOWN", 1.0) + press(kb, "KEY_BACKSPACE", 2.0) + press(kb, "KEY_KP5", 3.0)
    assert of(out, Gesture) == [Gesture("volume", "turn", 1.0, -1), Gesture("reset", "click", 2.05)]
    assert of(out, KeyPress) == [KeyPress("5", "digit", 3.0)]
    press(kb, "KEY_RIGHT", 4.0, length=0.001)
    assert of(kb.tick(4.05), Gesture) == [Gesture("knob", "turn", 4.05, 1)]   # arrows: one a press
    pad = Pipeline(s)
    for i in range(4):
        press(pad, "KEY_RIGHT", 5.0 + i * 0.002, length=0.001)
    assert of(pad.tick(5.05), Gesture) == [Gesture("knob", "turn", 5.05, 2)]
