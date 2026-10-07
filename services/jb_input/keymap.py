"""Stage 2: evdev key codes -> named controls, from config."""

from services.jb_input.events import ControlEvent, RawEvent


def selection_kind(control: str) -> str | None:
    """"key.B" -> "letter", "key.7" -> "digit", anything else -> None."""
    if not control.startswith("key."):
        return None
    return "digit" if control[4:].isdigit() else "letter"


class Keymap:
    def __init__(self, mapping: dict):
        self.mapping = dict(mapping)

    def translate(self, raw: RawEvent) -> ControlEvent | None:
        """None for auto-repeat (value 2) and for codes not in the map."""
        if raw.value not in (0, 1):
            return None
        control = self.mapping.get(raw.code)
        if control is None:
            return None
        return ControlEvent(control, "down" if raw.value == 1 else "up", raw.t)
