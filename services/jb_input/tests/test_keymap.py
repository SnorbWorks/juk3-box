from services.jb_input.config import DEFAULT_KEYMAP, DIGITS, LETTERS
from services.jb_input.events import RawEvent
from services.jb_input.keymap import Keymap, selection_kind

km = Keymap(DEFAULT_KEYMAP)


def test_all_24_codes_map_to_their_controls():
    expected = {f"KEY_{c}": f"key.{c}" for c in LETTERS + DIGITS}
    expected.update(KEY_ESC="reset", KEY_ENTER="knob.push", KEY_RIGHT="knob.cw", KEY_LEFT="knob.ccw")
    assert len(expected) == 24
    for code, control in expected.items():
        down = km.translate(RawEvent(code, 1, 1.0))
        up = km.translate(RawEvent(code, 0, 2.0))
        assert (down.control, down.state, down.t) == (control, "down", 1.0)
        assert (up.control, up.state) == (control, "up")


def test_no_letter_i():
    assert km.translate(RawEvent("KEY_I", 1, 0.0)) is None


def test_unknown_codes_are_ignored_without_error():
    assert km.translate(RawEvent("KEY_SPACE", 1, 0.0)) is None
    assert km.translate(RawEvent("KEY_999", 0, 0.0)) is None


def test_auto_repeat_is_ignored():
    assert km.translate(RawEvent("KEY_A", 2, 0.0)) is None
    assert km.translate(RawEvent("KEY_ESC", 2, 0.0)) is None


def test_selection_kind():
    assert selection_kind("key.B") == "letter"
    assert selection_kind("key.7") == "digit"
    assert selection_kind("key.0") == "digit"
    assert selection_kind("reset") is None
    assert selection_kind("knob.push") is None
