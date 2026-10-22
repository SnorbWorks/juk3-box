"""The selection format: how a song's code is written on this jukebox.

One setting decides what the keys can enter, how presses are read and how
the song board numbers its slots, so the three can't disagree
(docs/Controls Setup — Plan.md, "How a selection is written"). It lives in
the owner's settings (/var/lib/juk3-box/settings.json, "selection"); the
setup wizard's controls step writes it.

Two kinds:
  letters   a letter, then a number: {"kind": "letters", "letters": "ABCDEFGHJK",
            "digits": "1234567890"}. A page is every pair, letter by letter:
            A1 A2 ... A0 B1 ... K0.
  numbers   a fixed count of digits: {"kind": "numbers", "places": 3,
            "first": 100, "last": 299, "place_digits": ["0123456789", ...]}.
            A page is every number from first to last whose digits are each
            allowed in their place (Rock-Ola style).

The engine's twin is services/jb_ui/engine/js/selection.js; keep them alike.
"""

from dataclasses import dataclass

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "1234567890"            # the keypad's order: 1-9, then 0
ALL_PLACE = "0123456789"
MAX_PAGE = 300                   # more than the board can show legibly

DEFAULT = {"kind": "letters", "letters": "ABCDEFGHJK", "digits": "1234567890"}

PRESETS = {
    "100": {"label": "100 selections: A–K and 1–0", **DEFAULT},
    "160": {"label": "160 selections: A–V and 1–8 (Seeburg)", "kind": "letters",
            "letters": "ABCDEFGHJKLMNPQRSTUV", "digits": "12345678"},
    "rockola": {"label": "Numbers only: 100–299 (Rock-Ola)", "kind": "numbers", "places": 3,
                "first": 100, "last": 299, "place_digits": [ALL_PLACE] * 3},
}


def _ordered(chars: str, order: str) -> str:
    return "".join(c for c in order if c in set(chars))


def clean(raw) -> dict | None:
    """A usable format from the settings, normalized, or None."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind == "letters":
        letters, digits = raw.get("letters"), raw.get("digits")
        if not (isinstance(letters, str) and isinstance(digits, str)):
            return None
        if set(letters) - set(LETTERS) or set(digits) - set(DIGITS):
            return None
        letters, digits = _ordered(letters, LETTERS), _ordered(digits, DIGITS)
        if not letters or not digits or len(letters) * len(digits) > MAX_PAGE:
            return None
        return {"kind": "letters", "letters": letters, "digits": digits}
    if kind == "numbers":
        places, first, last = raw.get("places"), raw.get("first"), raw.get("last")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (places, first, last)):
            return None
        if not 1 <= places <= 4 or not 0 <= first <= last < 10 ** places:
            return None
        pd = raw.get("place_digits") or [ALL_PLACE] * places
        if not (isinstance(pd, list) and len(pd) == places and all(isinstance(p, str) and p for p in pd)):
            return None
        if any(set(p) - set(ALL_PLACE) for p in pd):
            return None
        fmt = {"kind": "numbers", "places": places, "first": first, "last": last,
               "place_digits": [_ordered(p, ALL_PLACE) for p in pd]}
        n = len(Format(fmt).codes)
        return fmt if 0 < n <= MAX_PAGE else None
    return None


def load(raw) -> "Format":
    """The format from the settings, or the default (A-K, 1-0)."""
    return Format(clean(raw) or DEFAULT)


@dataclass(frozen=True)
class Group:
    label: str
    codes: tuple


class Format:
    def __init__(self, spec: dict):
        self.spec = spec
        self.kind = spec["kind"]
        if self.kind == "letters":
            self.letters, self.digits = spec["letters"], spec["digits"]
            self.codes = [l + d for l in self.letters for d in self.digits]
        else:
            self.letters, self.places = "", spec["places"]
            self.place_digits = spec["place_digits"]
            self.digits = _ordered("".join(self.place_digits), DIGITS)
            self.codes = [c for c in (str(n).zfill(self.places) for n in range(spec["first"], spec["last"] + 1))
                          if all(ch in self.place_digits[i] for i, ch in enumerate(c))]
        self._index = {c: i for i, c in enumerate(self.codes)}

    @property
    def page_size(self) -> int:
        return len(self.codes)

    def index(self, code: str) -> int | None:
        return self._index.get(code)

    def is_prefix(self, typed: str) -> bool:
        """Some code starts with these digits (numbers)."""
        return any(c.startswith(typed) for c in self.codes)

    def groups(self) -> list:
        """The board's panels: a letter's codes, or ten numbers."""
        out: dict[str, list] = {}
        for c in self.codes:
            out.setdefault(c[0] if self.kind == "letters" else c[:-1], []).append(c)
        if self.kind == "letters":
            return [Group(k, tuple(v)) for k, v in out.items()]
        return [Group(f"{v[0]}–{v[-1]}", tuple(v)) for v in out.values()]

    def keys(self) -> list:
        """Every key the selections use, letters first."""
        return list(self.letters) + list(self.digits)

    def describe(self) -> str:
        """"100 songs a page, A1 to K0"."""
        return f"{self.page_size} songs a page, {self.codes[0]} to {self.codes[-1]}"
