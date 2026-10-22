"""Selection entry: key presses -> a slot code such as "B7" or "214".

A pure state machine, no I/O: it takes key presses and the time and returns
what to publish. jb-core feeds it from jukebox/jb-input/key and publishes
what it returns. What a code looks like is the jukebox's selection format
(shared/selection.py): letters and numbers, or numbers only.

Letters and numbers:
  - a selection is a letter FOLLOWED by a digit; a digit with no letter
    waiting is rejected (letter-first only, per the user) -- reported so the
    button lights can flag it, but it changes no state
  - a second letter replaces the first (B then C -> C waiting)
  - letter + digit emits the selection and returns to idle
Numbers only:
  - digits collect until there are as many as a code has; one that no code
    can start with is rejected and the digits so far stay
Both:
  - a key the format doesn't use (an I, a 9 on a 1-8 jukebox, any letter on
    a numbers-only one) is rejected: "not_here"
  - a partial entry left for timeout_ms with no key is cleared
  - cancel clears a partial entry; with nothing entered it does nothing

The slot index is the code's place on a board page (Format.index): A1 = 0,
B7 = 16, K0 = 99 with A-K and 1-0 -- the same order as the board's slots.
"""

from dataclasses import dataclass

from shared.selection import DEFAULT, Format

_DEFAULT = Format(DEFAULT)
LETTERS = _DEFAULT.letters
DIGITS = _DEFAULT.digits


def slot_index(letter: str, digit: str) -> int:
    """A-K, 1-0's index (the default format)."""
    return _DEFAULT.index(letter + digit)


@dataclass(frozen=True)
class EntryChanged:
    """Published on jukebox/jb-core/entry (retained)."""
    state: str            # "idle" | "partial"
    letter: str | None    # the waiting letter (letters and numbers)
    reason: str | None    # "selected" | "cancelled" | "timeout" when returning to idle
    typed: str | None = None   # what's entered so far: the letter, or the digits

    def payload(self) -> dict:
        return {"state": self.state, "letter": self.letter, "reason": self.reason, "typed": self.typed}


@dataclass(frozen=True)
class Rejected:
    """A key that can't be used right now. Published on jukebox/jb-core/rejected.
    reason: "no_letter" (a digit first), "not_here" (a key this jukebox's
    selections don't use), "no_such_number" (no code starts like that)."""
    key: str
    reason: str

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
    def __init__(self, timeout_ms: int = 10000, fmt: Format | None = None):
        self.timeout = timeout_ms / 1000
        self.fmt = fmt or _DEFAULT
        self.typed = ""
        self.last_t: float | None = None

    @property
    def letter(self) -> str | None:
        return self.typed if self.fmt.kind == "letters" and self.typed else None

    @property
    def state(self) -> str:
        return "partial" if self.typed else "idle"

    def set_format(self, fmt: Format) -> list:
        """A new format (the owner changed it): anything half-entered goes."""
        self.fmt = fmt
        if self.state == "idle":
            return []
        self._clear()
        return [EntryChanged("idle", None, "cancelled")]

    def key(self, key: str, t: float) -> list:
        """A letter or digit pressed. Returns events to publish."""
        if not (len(key) == 1 and key.isascii() and key.isalnum()):
            raise ValueError(f"not a selection key: {key!r}")
        key = key.upper()
        if self.fmt.kind == "letters":
            return self._letters_key(key, t)
        return self._numbers_key(key, t)

    def _letters_key(self, key, t):
        if key.isalpha():
            if key not in self.fmt.letters:
                return [Rejected(key, "not_here")]
            self.typed, self.last_t = key, t
            return [EntryChanged("partial", key, None, key)]
        if not self.typed:
            return [Rejected(key, "no_letter")]  # a digit can't come first
        if key not in self.fmt.digits:
            return [Rejected(key, "not_here")]
        code = self.typed + key
        self._clear()
        return [Selection(code, self.fmt.index(code)), EntryChanged("idle", None, "selected")]

    def _numbers_key(self, key, t):
        if key.isalpha():
            return [Rejected(key, "not_here")]
        typed = self.typed + key
        if not self.fmt.is_prefix(typed):
            return [Rejected(key, "no_such_number" if key in self.fmt.digits else "not_here")]
        if len(typed) == self.fmt.places:
            self._clear()
            return [Selection(typed, self.fmt.index(typed)), EntryChanged("idle", None, "selected")]
        self.typed, self.last_t = typed, t
        return [EntryChanged("partial", None, None, typed)]

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
        self.typed = ""
        self.last_t = None
