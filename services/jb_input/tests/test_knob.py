from services.jb_input.events import Gesture
from services.jb_input.gestures import ButtonGestures
from services.jb_input.knob import Knob


def make(push_turn=False):
    push = ButtonGestures("knob", "knob_push_stuck", 300, 700, 30000)
    return Knob(coalesce_ms=20, push=push, push_turn_enabled=push_turn), push


def test_three_taps_inside_20ms_give_one_turn_of_3():
    knob, _ = make()
    out = knob.step(+1, 0.000) + knob.step(+1, 0.005) + knob.step(+1, 0.010)
    assert out == []
    assert knob.tick(0.021) == [Gesture("knob", "turn", 0.021, 3)]


def test_mixed_directions_sum_with_sign():
    knob, _ = make()
    knob.step(-1, 0.0), knob.step(-1, 0.005), knob.step(+1, 0.01), knob.step(-1, 0.015)
    assert knob.tick(0.03) == [Gesture("knob", "turn", 0.03, -2)]


def test_steps_cancelling_out_produce_nothing():
    knob, _ = make()
    knob.step(+1, 0.0), knob.step(-1, 0.005)
    assert knob.tick(0.03) == []


def test_steps_outside_the_window_are_separate_gestures():
    knob, _ = make()
    out = knob.step(+1, 0.0) + knob.step(+1, 0.05)  # second step starts a new burst
    assert out == [Gesture("knob", "turn", 0.05, 1)]
    assert knob.tick(0.1) == [Gesture("knob", "turn", 0.1, 1)]


def test_default_turning_while_pushed_is_a_plain_turn_and_release_is_a_click():
    knob, push = make()
    push.down(0.0)
    knob.step(+1, 0.1)
    assert knob.tick(0.13) == [Gesture("knob", "turn", 0.13, 1)]
    assert [e.gesture for e in push.up(0.5) if isinstance(e, Gesture)] == ["click"]


def test_push_turn_enabled_step_while_pushed_is_push_turn_and_consumes_the_push():
    knob, push = make(push_turn=True)
    push.down(0.0)
    knob.step(+1, 0.1), knob.step(+1, 0.105)
    assert knob.tick(0.13) == [Gesture("knob", "push_turn", 0.13, 2)]
    out = push.tick(1.0) + push.up(1.0)
    assert [e for e in out if isinstance(e, Gesture)] == []


def test_push_turn_enabled_push_mid_burst_splits_turn_from_push_turn():
    knob, push = make(push_turn=True)
    knob.step(+1, 0.0)
    push.down(0.005)
    out = knob.step(+1, 0.01)
    assert out == [Gesture("knob", "turn", 0.01, 1)]
    assert knob.tick(0.04) == [Gesture("knob", "push_turn", 0.04, 1)]
