"""Selection entry: a letter, then a digit -> a slot such as "B7".

A pure state machine, no I/O: it takes key presses and the time and returns
what to publish. jb-core feeds it from jukebox/jb-input/key and publishes
what it returns.

Rules:
  - a selection is a letter FOLLOWED by a digit; a digit with no letter
    waiting is rejected (letter-first only, per the user) -- reported so the
    button lights can flag it, but it changes no state
  - a second letter replaces the first (B then C -> C waiting)
  - letter + digit emits the selection and returns to idle
  - a waiting letter left for timeout_ms with no key is cleared
  - cancel clears a waiting letter; with nothing entered it does nothing

Slot index = letter index * 10 + digit index, where letters A B C D E F G H J
K (no I) are 0-9 and digits 1-9 are 0-8 and 0 is 9. So A1 = 0, B7 = 16,
K0 = 99 -- the same order as the board's slots.
"""

from dataclasses import dataclass

LETTERS = "ABCDEFGHJK"
DIGITS = "1234567890"


def slot_index(letter: str, digit: str) -> int:
    return LETTERS.index(letter) * 10 + DIGITS.index(digit)


@dataclass(frozen=True)
class EntryChanged:
    """Published on jukebox/jb-core/entry (retained)."""
    state: str            # "idle" | "partial"
    letter: str | None
    reason: str | None    # "selected" | "cancelled" | "timeout" when returning to idle

    def payload(self) -> dict:
        return {"state": self.state, "letter": self.letter, "reason": self.reason}


@dataclass(frozen=True)
class Rejected:
    """A key that can't be used right now (a digit with no letter waiting).
    Published on jukebox/jb-core/rejected."""
    key: str
    reason: str           # "no_letter"

    def payload(self) -> dict:
        return {"key": self.key, "reason": self.reason}


@dataclass(frozen=True)
class Selection:
    """Published on jukebox/jb-core/selection."""
    slot: str
    index: int

    def payload(self) -> dict:
        return {"slot": self.slot, "index": self.index}


class Entry:
    def __init__(self, timeout_ms: int = 10000):
        self.timeout = timeout_ms / 1000
        self.letter: str | None = None
        self.last_t: float | None = None

    @property
    def state(self) -> str:
        return "partial" if self.letter else "idle"

    def key(self, key: str, t: float) -> list:
        """A letter or digit pressed. Returns events to publish."""
        if key in LETTERS:
            self.letter, self.last_t = key, t
            return [EntryChanged("partial", key, None)]
        if key in DIGITS:
            if self.letter is None:
                return [Rejected(key, "no_letter")]  # a digit can't come first
            selection = Selection(self.letter + key, slot_index(self.letter, key))
            self._clear()
            return [selection, EntryChanged("idle", None, "selected")]
        raise ValueError(f"not a selection key: {key!r}")

    def cancel(self, t: float) -> list:
        if self.state == "idle":
            return []
        self._clear()
        return [EntryChanged("idle", None, "cancelled")]

    def tick(self, t: float) -> list:
        if self.state == "partial" and t - self.last_t >= self.timeout:
            self._clear()
            return [EntryChanged("idle", None, "timeout")]
        return []

    def _clear(self):
        self.letter = None
        self.last_t = None
