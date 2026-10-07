"""Selection entry: one letter + one digit -> a slot such as "B7".

A pure state machine, no I/O: it takes events and the time and returns what
to publish. jb-core feeds it from jb/input/key (and entry.cancel actions)
and publishes jb/core/entry and jb/core/selection from what it returns.

Rules (from the keyboard build brief):
  - a selection is one letter and one digit, in either order
  - a second key of the same kind replaces the first (B then C -> C waiting)
  - when both are present, emit the selection and return to idle
  - a partial entry left for timeout_ms with no key is cleared
  - cancel clears a partial entry; with nothing entered it does nothing

Slot index = letter index * 10 + digit index, where letters A B C D E F G H J
K (no I) are 0-9 and digits 1-9 are 0-8 and 0 is 9. So A1 = 0, B7 = 16,
K0 = 99.
"""

from dataclasses import dataclass

LETTERS = "ABCDEFGHJK"
DIGITS = "1234567890"


def slot_index(letter: str, digit: str) -> int:
    return LETTERS.index(letter) * 10 + DIGITS.index(digit)


@dataclass(frozen=True)
class EntryChanged:
    """Published on jb/core/entry (retained)."""
    state: str            # "idle" | "partial"
    letter: str | None
    digit: str | None
    reason: str | None    # "selected" | "cancelled" | "timeout" when returning to idle

    def payload(self) -> dict:
        return {"state": self.state, "letter": self.letter, "digit": self.digit, "reason": self.reason}


@dataclass(frozen=True)
class Selection:
    """Published on jb/core/selection."""
    slot: str
    index: int

    def payload(self) -> dict:
        return {"slot": self.slot, "index": self.index}


class Entry:
    def __init__(self, timeout_ms: int = 10000):
        self.timeout = timeout_ms / 1000
        self.letter: str | None = None
        self.digit: str | None = None
        self.last_t: float | None = None

    @property
    def state(self) -> str:
        return "partial" if (self.letter or self.digit) else "idle"

    def key(self, key: str, t: float) -> list:
        """A letter or digit pressed. Returns events to publish."""
        if key in LETTERS:
            self.letter = key
        elif key in DIGITS:
            self.digit = key
        else:
            raise ValueError(f"not a selection key: {key!r}")
        self.last_t = t
        if self.letter and self.digit:
            selection = Selection(self.letter + self.digit, slot_index(self.letter, self.digit))
            self._clear()
            return [selection, EntryChanged("idle", None, None, "selected")]
        return [self._changed()]

    def cancel(self, t: float) -> list:
        if self.state == "idle":
            return []
        self._clear()
        return [EntryChanged("idle", None, None, "cancelled")]

    def tick(self, t: float) -> list:
        if self.state == "partial" and t - self.last_t >= self.timeout:
            self._clear()
            return [EntryChanged("idle", None, None, "timeout")]
        return []

    def _changed(self) -> EntryChanged:
        return EntryChanged(self.state, self.letter, self.digit, None)

    def _clear(self):
        self.letter = self.digit = None
        self.last_t = None
