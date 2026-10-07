"""The pure part of jb-input, end to end: raw key events in, publishable
events out. No I/O and no clock of its own -- feed() and tick() take the time,
so tests can drive the whole chain with a fake clock.

Output types (see events.py): ControlEvent (every control change, published
only when publish_raw is on), KeyPress, Gesture, Action, FaultChange.
"""

from services.jb_input.actions import ActionTable
from services.jb_input.config import Settings
from services.jb_input.events import FaultChange, Gesture, KeyPress, RawEvent
from services.jb_input.gestures import ButtonGestures
from services.jb_input.keymap import Keymap, selection_kind
from services.jb_input.knob import Knob


class Pipeline:
    def __init__(self, settings: Settings):
        self.keymap = Keymap(settings.keymap)
        self.actions = ActionTable(settings.actions)

        def button(control, fault):
            enabled = settings.gestures.get(control, [])
            return ButtonGestures(control, fault, settings.double_window_ms, settings.hold_ms,
                                  settings.stuck_ms, double_enabled="double" in enabled,
                                  hold_enabled="hold" in enabled)

        # control name in the keymap -> its gesture machine
        self.buttons = {
            "reset": button("reset", "reset_stuck"),
            "knob.push": button("knob", "knob_push_stuck"),
        }
        self.knob = Knob(settings.coalesce_ms, self.buttons["knob.push"], settings.push_turn)
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
        return out

    def start_held(self, codes, t) -> list:
        """Keys already held when the device appeared. A held Reset (or knob
        push) goes straight to the stuck fault, per the brief."""
        out = []
        for code in codes:
            control = self.keymap.mapping.get(code)
            if control in self.buttons:
                out += self._track(self.buttons[control].start_stuck(t))
        return out

    def feed(self, raw: RawEvent) -> list:
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
        elif ev.control in ("knob.cw", "knob.ccw") and ev.state == "down":
            out += self.knob.step(+1 if ev.control == "knob.cw" else -1, ev.t)
        return self._with_actions(out)

    def tick(self, t) -> list:
        out = []
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
