"""Stage 3: click (and optional double-click / hold) for one button (Reset or
knob push).

A pure state machine driven by down(), up() and tick(), each given the time.
It never sleeps or reads a clock itself, so tests can drive it with a fake
clock. Timings come from config.

  click   the default and, for now, the only gesture: fires on release
  hold    (opt-in) down for hold_ms; fires once while still held, and the
          later release produces nothing
  double  (opt-in) a second press within double_window_ms of the first
          release; fires on that second press (whose own release and hold
          then produce nothing). Enabling it makes click wait out the window.

Stuck: down for stuck_ms raises a fault (once) and the button stops producing
gestures until it is released, which clears the fault. On the normally closed
Reset, a broken wire looks exactly like this.
"""

from services.jb_input.events import FaultChange, Gesture

IDLE, DOWN, WAIT_SECOND = "idle", "down", "wait_second"


class ButtonGestures:
    def __init__(self, control: str, fault: str, double_window_ms: int, hold_ms: int,
                 stuck_ms: int, double_enabled: bool = False, hold_enabled: bool = False):
        self.control = control
        self.fault = fault
        self.double_window = double_window_ms / 1000
        self.hold = hold_ms / 1000
        self.stuck_after = stuck_ms / 1000
        self.double_enabled = double_enabled
        self.hold_enabled = hold_enabled
        self.reset_state()

    def reset_state(self):
        """Forget everything (used when the device reconnects). A raised
        stuck fault is left for the caller to clear."""
        self.state = IDLE
        self.down_t = 0.0
        self.release_t = 0.0
        self.consumed = False    # this press already used (double / push_turn)
        self.hold_fired = False
        self.stuck = False

    @property
    def is_down(self) -> bool:
        return self.state == DOWN

    def consume(self):
        """Mark the current press as used, e.g. by a push-and-turn: it then
        produces no click, double or hold."""
        if self.state == DOWN:
            self.consumed = True

    def start_stuck(self, t) -> list:
        """The button was already held when the device appeared (e.g. Reset at
        startup): treat it as stuck immediately."""
        self.state, self.down_t, self.consumed, self.hold_fired = DOWN, t, True, True
        self.stuck = True
        return [FaultChange(self.fault, True, t)]

    def down(self, t) -> list:
        out = []
        if self.state == WAIT_SECOND and t - self.release_t <= self.double_window:
            out.append(Gesture(self.control, "double", t))
            self.state, self.down_t, self.consumed, self.hold_fired = DOWN, t, True, False
            return out
        out += self._flush_pending(t)
        self.state, self.down_t, self.consumed, self.hold_fired = DOWN, t, False, False
        return out

    def up(self, t) -> list:
        if self.state != DOWN:
            return []
        out = []
        if self.stuck:
            self.stuck = False
            self.state = IDLE
            return [FaultChange(self.fault, False, t)]
        if self.consumed or self.hold_fired:
            self.state = IDLE
        elif self.double_enabled:
            self.state, self.release_t = WAIT_SECOND, t
        else:
            self.state = IDLE
            out.append(Gesture(self.control, "click", t))
        return out

    def tick(self, t) -> list:
        out = []
        if self.state == DOWN and not self.stuck:
            held = t - self.down_t
            if self.hold_enabled and not self.consumed and not self.hold_fired and held >= self.hold:
                self.hold_fired = True
                out.append(Gesture(self.control, "hold", t))
            if held >= self.stuck_after:
                self.stuck = True
                out.append(FaultChange(self.fault, True, t))
        out += self._flush_pending(t)
        return out

    def _flush_pending(self, t) -> list:
        """A first click whose double-click window has run out becomes a click."""
        if self.state == WAIT_SECOND and t - self.release_t > self.double_window:
            self.state = IDLE
            return [Gesture(self.control, "click", t)]
        return []
