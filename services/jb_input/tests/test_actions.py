from services.jb_input.actions import ActionTable
from services.jb_input.config import Settings
from services.jb_input.events import Action, Gesture


def test_nothing_is_assigned_by_default():
    table = ActionTable(Settings().actions)
    for control, gesture in [("knob", "click"), ("knob", "turn"), ("reset", "click")]:
        assert table.lookup(Gesture(control, gesture, 1.0)) is None


def test_an_assigned_gesture_produces_its_action_with_steps_as_value():
    table = ActionTable({("knob", "turn"): "volume.step", ("reset", "click"): "x.y"})
    assert table.lookup(Gesture("knob", "turn", 1.0, 3)) == Action("volume.step", 3, "knob", "turn", 1.0)
    assert table.lookup(Gesture("reset", "click", 1.0)) == Action("x.y", None, "reset", "click", 1.0)
    assert table.lookup(Gesture("knob", "click", 1.0)) is None
