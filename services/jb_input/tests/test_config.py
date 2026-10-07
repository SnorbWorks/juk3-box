import json

import pytest

from services.jb_input import config


def write(tmp_path, data):
    p = tmp_path / "jb-input.json"
    p.write_text(json.dumps(data))
    return p


def test_defaults_without_a_file(tmp_path):
    s = config.load(tmp_path / "missing.json")
    assert (s.double_window_ms, s.hold_ms, s.stuck_ms, s.coalesce_ms) == (300, 700, 30000, 20)
    assert s.grab is True and s.publish_raw is False and s.push_turn is False
    assert s.gestures == {"reset": [], "knob": []}  # click only
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
    {"keymap": {"KEY_Z": "key.Z"}},
])
def test_bad_config_fails_loudly(tmp_path, data):
    with pytest.raises(config.ConfigError):
        config.load(write(tmp_path, data))
