"""The pure part of jb-input, end to end: raw key events in, publishable
events out. No I/O and no clock of its own -- feed() and tick() take the time,
so tests can drive the whole chain with a fake clock.

Output types (see events.py): ControlEvent (every control change, published
only when publish_raw is on), KeyPress, Gesture, Action, FaultChange.

One pipeline per device: a keypad uses the learned keymap, knob calibration
and settle times; a keyboard the keyboard keymap, one step a click, and the
plain settle time.

Settle (debounce): after a key or button changes, changes of the same one
within its settle time are a bouncing switch, not a person, and are dropped;
if it ends up different when the time is over (a quick tap released inside
it), tick() catches up. The knob's steps aren't settled: they come fast.
"""

from services.jb_input.actions import ActionTable
from services.jb_input.config import KEYBOARD_KEYMAP, Settings
from services.jb_input.events import FaultChange, Gesture, KeyPress, RawEvent
from services.jb_input.gestures import ButtonGestures
from services.jb_input.keymap import Keymap, selection_kind
from services.jb_input.knob import Knob


KNOB_STEPS = ("knob.cw", "knob.ccw")


class Pipeline:
    def __init__(self, settings: Settings, role: str = "keypad"):
        keypad = role != "keyboard"
        self.keymap = Keymap(settings.keymap if keypad else KEYBOARD_KEYMAP)
        self.actions = ActionTable(settings.actions)
        self.settle = settings.settle_ms / 1000
        self.settle_by_code = {c: ms / 1000 for c, ms in settings.settle_by_code.items()} if keypad else {}
        self.level: dict[str, int] = {}     # code -> 1/0, as accepted
        self.changed: dict[str, float] = {}  # code -> when it was accepted
        self.pending: dict[str, int] = {}   # code -> where it ended up while settling

        def button(control, fault):
            enabled = settings.gestures.get(control, [])
            return ButtonGestures(control, fault, settings.double_window_ms, settings.hold_ms,
                                  settings.stuck_ms, double_enabled="double" in enabled,
                                  hold_enabled="hold" in enabled,
                                  long_hold_ms=settings.long_hold_ms,
                                  long_hold_enabled="long_hold" in enabled)

        # control name in the keymap -> its gesture machine
        self.buttons = {
            "reset": button("reset", "reset_stuck"),
            "knob.push": button("knob", "knob_push_stuck"),
        }
        self.knob = Knob(settings.coalesce_ms, self.buttons["knob.push"], settings.push_turn,
                         settings.knob_steps_per_click if keypad else 1)
        self.faults: set[str] = set()

    def reset_state(self, t) -> list:
        """Device (re)connected: forget held keys and pending gestures. Any
        raised stuck faults are cleared too -- if the button is still held,
        start_held() raises them again."""
        out = [FaultChange(f, False, t) for f in sorted(self.faults)]
        self.faults.clear()
        for b in self.buttons.values():
            b.reset_state()
        self.knob.reset_state()
        self.level.clear()
        self.changed.clear()
        self.pending.clear()
        return out

    def start_held(self, codes, t) -> list:
        """Keys already held when the device appeared. A held Reset (or knob
        push) goes straight to the stuck fault, per the brief."""
        out = []
        for code in codes:
            self.level[code], self.changed[code] = 1, t   # so letting go counts
            control = self.keymap.mapping.get(code)
            if control in self.buttons:
                out += self._track(self.buttons[control].start_stuck(t))
        return out

    def feed(self, raw: RawEvent) -> list:
        if raw.value in (0, 1) and self.keymap.mapping.get(raw.code) not in (None, *KNOB_STEPS):
            if raw.value == self.level.get(raw.code, 0):
                self.pending.pop(raw.code, None)   # bounced back to where it was
                return []
            settle = self.settle_by_code.get(raw.code, self.settle)
            if raw.t - self.changed.get(raw.code, float("-inf")) < settle:
                self.pending[raw.code] = raw.value
                return []
            self.level[raw.code], self.changed[raw.code] = raw.value, raw.t
        return self._feed(raw)

    def _feed(self, raw: RawEvent) -> list:
        ev = self.keymap.translate(raw)
        if ev is None:
            return []
        out: list = [ev]
        kind = selection_kind(ev.control)
        if kind is not None:
            if ev.state == "down":  # instant, no gesture delay; key up is ignored
                out.append(KeyPress(ev.control[4:], kind, ev.t))
        elif ev.control in self.buttons:
            b = self.buttons[ev.control]
            out += self._track(b.down(ev.t) if ev.state == "down" else b.up(ev.t))
        elif ev.control in KNOB_STEPS and ev.state == "down":
            out += self.knob.step(+1 if ev.control == "knob.cw" else -1, ev.t)
        elif ev.control in ("volume.up", "volume.down") and ev.state == "down":
            out.append(Gesture("volume", "turn", ev.t, 1 if ev.control == "volume.up" else -1))
        return self._with_actions(out)

    def tick(self, t) -> list:
        out = []
        for code, value in list(self.pending.items()):
            if t - self.changed[code] >= self.settle_by_code.get(code, self.settle):
                del self.pending[code]
                self.level[code], self.changed[code] = value, t
                out += self._feed(RawEvent(code, value, t))
        for b in self.buttons.values():
            out += self._track(b.tick(t))
        out += self.knob.tick(t)
        return self._with_actions(out)

    def _track(self, events) -> list:
        for e in events:
            if isinstance(e, FaultChange):
                (self.faults.add if e.active else self.faults.discard)(e.fault)
        return events

    def _with_actions(self, events) -> list:
        out = []
        for e in events:
            out.append(e)
            if isinstance(e, Gesture):
                action = self.actions.lookup(e)
                if action is not None:
                    out.append(action)
        return out
