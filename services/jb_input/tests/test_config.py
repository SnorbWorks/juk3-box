import json

import pytest

from services.jb_input import config


def write(tmp_path, data):
    p = tmp_path / "jb-input.json"
    p.write_text(json.dumps(data))
    return p


def test_defaults_without_a_file(tmp_path):
    s = config.load(tmp_path / "missing.json")
    assert (s.double_window_ms, s.hold_ms, s.long_hold_ms, s.stuck_ms, s.coalesce_ms) == (300, 1000, 4000, 30000, 20)
    assert s.grab is True and s.publish_raw is False and s.push_turn is False
    assert s.gestures == {"reset": ["hold", "long_hold"], "knob": []}  # Reset: 1 s skip, 4 s power menu
    assert s.actions == {}                          # nothing assigned yet


def test_json_overrides_values_and_tables(tmp_path):
    s = config.load(write(tmp_path, {
        "hold_ms": 900, "reconnect_s": 5, "push_turn": True,
        "gestures": {"knob": ["hold", "double"]},
        "actions": {"knob.hold": "menu.open"},
    }))
    assert s.hold_ms == 900 and s.reconnect_s == 5.0 and s.push_turn is True
    assert s.gestures == {"reset": [], "knob": ["hold", "double"]}
    assert s.actions == {("knob", "hold"): "menu.open"}


@pytest.mark.parametrize("data", [
    {"hold_ms": "slow"},
    {"no_such_setting": 1},
    {"coalesce_ms": 0},
    {"actions": {"knob.spin": "x"}},
    {"actions": {"knob.click": ""}},
    {"gestures": {"knob": ["triple"]}},
    {"gestures": {"pedal": []}},
    {"keymap": {"KEY_Z": "key.Ä"}},
    {"knob_steps_per_click": 0},
])
def test_bad_config_fails_loudly(tmp_path, data):
    with pytest.raises(config.ConfigError):
        config.load(write(tmp_path, data))


def test_long_hold_must_be_longer_than_hold(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load(write(tmp_path, {"hold_ms": 4000, "long_hold_ms": 1000}))


def controls(tmp_path, data):
    p = tmp_path / "controls.json"
    p.write_text(json.dumps(data))
    return p


def test_learned_controls_lay_over_the_settings(tmp_path):
    s = config.load_controls(config.Settings(), controls(tmp_path, {
        "keypads": ["16c0:27db:Arcade Board"], "keymap": {"KEY_Q": "key.A", "KEY_W": "knob.cw"},
        "knob_steps_per_click": 4, "settle_ms": {"KEY_Q": 90}}))
    assert s.keypads == ["16c0:27db:Arcade Board"]
    assert s.keymap == {"KEY_Q": "key.A", "KEY_W": "knob.cw"}
    assert (s.knob_steps_per_click, s.settle_by_code) == (4, {"KEY_Q": 90})


def test_no_controls_file_keeps_the_defaults(tmp_path):
    s = config.load_controls(config.Settings(), tmp_path / "none.json")
    assert s.keymap == config.DEFAULT_KEYMAP and s.keypads == [] and s.knob_steps_per_click == 1


@pytest.mark.parametrize("data", [
    [], {"keymap": {"KEY_Q": "launch"}}, {"keypads": "pico"},
    {"knob_steps_per_click": 9}, {"settle_ms": {"KEY_Q": 900}},
])
def test_bad_controls_file_raises(tmp_path, data):
    with pytest.raises(config.ConfigError):
        config.load_controls(config.Settings(), controls(tmp_path, data))
