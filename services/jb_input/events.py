"""Event types passed between jb-input's pipeline stages.

All times (`t`) are seconds from time.monotonic() (or a test's fake clock);
the publisher converts them to integer milliseconds for the wire.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RawEvent:
    """One evdev key event from the keypad: code name and 1 (down) / 0 (up)."""
    code: str     # e.g. "KEY_A"
    value: int    # 1 = down, 0 = up (2 = auto-repeat, dropped by the keymap)
    t: float


@dataclass(frozen=True)
class ControlEvent:
    """A raw event translated to a named control."""
    control: str  # e.g. "key.B", "reset", "knob.push", "knob.cw"
    state: str    # "down" | "up"
    t: float


@dataclass(frozen=True)
class KeyPress:
    """A selection key going down -- published immediately on jb/input/key."""
    key: str      # "A".."K" (no I) or "0".."9"
    kind: str     # "letter" | "digit"
    t: float


@dataclass(frozen=True)
class Gesture:
    control: str  # "reset" | "knob"
    gesture: str  # "click" | "double" | "hold" | "turn" | "push_turn"
    t: float
    steps: int | None = None  # only on "turn" / "push_turn"


@dataclass(frozen=True)
class Action:
    action: str   # e.g. "volume.step"
    value: int | None
    control: str
    gesture: str
    t: float


@dataclass(frozen=True)
class FaultChange:
    """A fault being raised (active=True) or cleared."""
    fault: str    # "reset_stuck" | "knob_push_stuck"
    active: bool
    t: float
