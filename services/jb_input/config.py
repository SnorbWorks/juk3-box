"""jb-input settings: built-in defaults, optionally overridden by
/etc/juk3-box/jb-input.json (the same place and format as the other jb-*
services). Validated on load so a typo fails loudly at startup.

Canon for now: the knob turns and clicks, the selection keys and Reset just
report presses. Advanced gestures (hold, double-click, push-and-turn) and the
gesture -> action table exist but are OFF unless enabled here.

What the setup wizard's controls step learns (docs/Controls Setup — Plan.md)
is kept apart, in /var/lib/juk3-box/controls.json (jb-admin writes it):
which devices are the cabinet's keypads, what each of their keys means, how
many steps one knob click sends, and settle times for bouncy buttons.
load_controls() lays it over the settings.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

SERVICE_NAME = "jb-input"
DEFAULT_PATH = Path(f"/etc/juk3-box/{SERVICE_NAME}.json")

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # which ones a jukebox uses is its selection format
DIGITS = "1234567890"    # 1-9, then 0

# A keypad nobody has taught yet: the Pico's keys (A-K without I, 1-0, Esc,
# Enter, arrows; "Keyboard Input Build Brief.md") and any other letter.
DEFAULT_KEYMAP = {
    **{f"KEY_{c}": f"key.{c}" for c in LETTERS},
    **{f"KEY_{d}": f"key.{d}" for d in DIGITS},
    "KEY_ESC": "reset",
    "KEY_ENTER": "knob.push",
    "KEY_RIGHT": "knob.cw",
    "KEY_LEFT": "knob.ccw",
}

# Any other keyboard plugged in, always: letters and numbers pick songs,
# arrows turn the knob, Enter presses it, Backspace and Esc cancel, up and
# down are volume.
KEYBOARD_KEYMAP = {
    **DEFAULT_KEYMAP,
    **{f"KEY_KP{d}": f"key.{d}" for d in DIGITS},
    "KEY_BACKSPACE": "reset",
    "KEY_KPENTER": "knob.push",
    "KEY_UP": "volume.up",
    "KEY_DOWN": "volume.down",
}

BUTTON_CONTROLS = ("reset", "knob")
BUTTON_GESTURES = ("click", "double", "hold", "long_hold")   # click is always on
GESTURES = BUTTON_GESTURES + ("turn", "push_turn")
VALID_CONTROL_NAMES = {f"key.{c}" for c in LETTERS + DIGITS} | {
    "reset", "knob.push", "knob.cw", "knob.ccw", "volume.up", "volume.down"}
CONTROLS_PATH = "/var/lib/juk3-box/controls.json"


@dataclass
class Settings:
    device_name: str = "Jukebox JB Keypad"    # our Pico; newer ones add their version after it
    device_path: str = "/dev/input/jb-keypad"
    keyboards: bool = True       # other keyboards work too (KEYBOARD_KEYMAP)
    controls_path: str = CONTROLS_PATH
    reconnect_s: float = 2.0
    grab: bool = True
    # Extra button gestures beyond click, per control: any of "double",
    # "hold", "long_hold". Reset: hold (1 s) cancels the playing song, long_hold
    # (4 s) opens the reboot/shut-down menu (the user's design, 2026-10-12).
    gestures: dict = field(default_factory=lambda: {"reset": ["hold", "long_hold"], "knob": []})
    push_turn: bool = False      # turning while the knob is pushed -> push_turn (else plain turn)
    double_window_ms: int = 300
    hold_ms: int = 1000
    long_hold_ms: int = 4000
    stuck_ms: int = 30000
    coalesce_ms: int = 20
    # A press of the same key or button sooner than this after the last one
    # is a bouncing switch, not a person: dropped. Per key in settle_by_code.
    settle_ms: int = 40
    settle_by_code: dict = field(default_factory=dict)
    knob_steps_per_click: int = 1   # what one knob click sends (1, 2 or 4 by encoder)
    keypads: list = field(default_factory=list)   # learned keypads' device ids
    publish_raw: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    keymap: dict = field(default_factory=lambda: dict(DEFAULT_KEYMAP))
    # (control, gesture) -> action name. Empty: nothing is assigned yet.
    actions: dict = field(default_factory=dict)


class ConfigError(ValueError):
    pass


def _parse_actions(table: dict) -> dict:
    """JSON form: {"knob.click": "some.action"}."""
    actions = {}
    for key, action in table.items():
        control, _, gesture = key.partition(".")
        if control not in BUTTON_CONTROLS or gesture not in GESTURES:
            raise ConfigError(f"actions: unknown control.gesture {key!r}")
        if not isinstance(action, str) or not action:
            raise ConfigError(f"actions: {key!r} must be a non-empty string")
        actions[(control, gesture)] = action
    return actions


def _parse_gestures(table: dict) -> dict:
    gestures = {"reset": [], "knob": []}
    for control, names in table.items():
        if control not in BUTTON_CONTROLS:
            raise ConfigError(f"gestures: unknown control {control!r}")
        bad = [n for n in names if n not in ("double", "hold", "long_hold")]
        if bad:
            raise ConfigError(f"gestures.{control}: only 'double', 'hold' and 'long_hold' can be enabled, got {bad}")
        gestures[control] = list(names)
    return gestures


def load(path: Path | str | None = None) -> Settings:
    settings = Settings()
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        return settings
    data = json.loads(path.read_text())

    keymap = data.pop("keymap", None)
    actions = data.pop("actions", None)
    gestures = data.pop("gestures", None)
    for key, value in data.items():
        if not hasattr(settings, key):
            raise ConfigError(f"unknown setting {key!r}")
        expected = type(getattr(settings, key))
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(f"{key!r} must be {expected.__name__}, got {value!r}")
        setattr(settings, key, value)

    if keymap is not None:
        for code, control in keymap.items():
            if control not in VALID_CONTROL_NAMES:
                raise ConfigError(f"keymap: {code!r} -> unknown control {control!r}")
        settings.keymap = dict(keymap)
    if actions is not None:
        settings.actions = _parse_actions(actions)
    if gestures is not None:
        settings.gestures = _parse_gestures(gestures)
    for name in ("double_window_ms", "hold_ms", "long_hold_ms", "stuck_ms", "coalesce_ms"):
        if getattr(settings, name) <= 0:
            raise ConfigError(f"{name!r} must be positive")
    if settings.long_hold_ms <= settings.hold_ms:
        raise ConfigError("'long_hold_ms' must be longer than 'hold_ms'")
    if settings.knob_steps_per_click < 1:
        raise ConfigError("'knob_steps_per_click' must be at least 1")
    return settings


def load_controls(settings: Settings, path: Path | str | None = None) -> Settings:
    """The learned controls laid over the settings: {"keypads": [device id,
    ...], "keymap": {"KEY_A": "key.A", ...}, "knob_steps_per_click": 2,
    "settle_ms": {"KEY_A": 80}}. A learned keymap replaces the keypad's
    default one. A file that doesn't make sense raises ConfigError; the
    caller keeps what it had (a wrong file mustn't stop the keypad)."""
    path = Path(path or settings.controls_path)
    if not path.exists():
        return settings
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ConfigError(f"{path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: not an object")
    keymap = data.get("keymap")
    if keymap is not None:
        if not isinstance(keymap, dict) or not all(isinstance(k, str) for k in keymap):
            raise ConfigError("controls: keymap must map key codes to controls")
        bad = {k: v for k, v in keymap.items() if v not in VALID_CONTROL_NAMES}
        if bad:
            raise ConfigError(f"controls: unknown controls {bad}")
    keypads = data.get("keypads", [])
    if not (isinstance(keypads, list) and all(isinstance(k, str) for k in keypads)):
        raise ConfigError("controls: keypads must be a list of device ids")
    per_click = data.get("knob_steps_per_click", settings.knob_steps_per_click)
    if not (isinstance(per_click, int) and not isinstance(per_click, bool) and 1 <= per_click <= 8):
        raise ConfigError("controls: knob_steps_per_click must be 1 to 8")
    settle = data.get("settle_ms", {})
    if not (isinstance(settle, dict) and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 500
                                             for v in settle.values())):
        raise ConfigError("controls: settle_ms must map key codes to 0-500 ms")
    if keymap is not None:
        settings.keymap = dict(keymap)
    settings.keypads = list(keypads)
    settings.knob_steps_per_click = per_click
    settings.settle_by_code = dict(settle)
    return settings
