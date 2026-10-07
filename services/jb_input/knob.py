"""Stage 3b: knob turning -> turn (and optional push_turn) gestures.

Each knob.cw / knob.ccw tap is one step of +1 / -1. Steps arriving within
coalesce_ms of the first step of a burst are merged into one gesture with
the signed total (e.g. turn, steps=+3).

push_turn is opt-in (off for now): when enabled, steps while the knob's push
button is down become push_turn, and that press is consumed -- it produces no
click, double or hold when it ends. When off, they're plain turns.
"""

from services.jb_input.events import Gesture
from services.jb_input.gestures import ButtonGestures


class Knob:
    def __init__(self, coalesce_ms: int, push: ButtonGestures, push_turn_enabled: bool = False):
        self.coalesce = coalesce_ms / 1000
        self.push = push
        self.push_turn_enabled = push_turn_enabled
        self.reset_state()

    def reset_state(self):
        self.steps = 0
        self.first_t = None
        self.kind = None  # "turn" | "push_turn" for the burst in progress

    def step(self, direction: int, t) -> list:
        kind = "push_turn" if self.push_turn_enabled and self.push.is_down else "turn"
        out = []
        # A new burst starts if the window has passed or the kind changed.
        if self.first_t is not None and (t - self.first_t >= self.coalesce or kind != self.kind):
            out += self._flush(t)
        if self.first_t is None:
            self.first_t, self.kind = t, kind
        self.steps += direction
        if kind == "push_turn":
            self.push.consume()
        return out

    def tick(self, t) -> list:
        if self.first_t is not None and t - self.first_t >= self.coalesce:
            return self._flush(t)
        return []

    def _flush(self, t) -> list:
        steps, kind = self.steps, self.kind
        self.reset_state()
        if steps == 0:  # e.g. +1 then -1 inside one window
            return []
        return [Gesture("knob", kind, t, steps)]
