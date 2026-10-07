from services.jb_input.events import FaultChange, Gesture
from services.jb_input.gestures import ButtonGestures


def button(double=False, hold=False):
    return ButtonGestures("reset", "reset_stuck", double_window_ms=300, hold_ms=700,
                          stuck_ms=30000, double_enabled=double, hold_enabled=hold)


def run_until(b, t_end, t_start, step=0.01):
    """Tick from t_start to t_end, collecting output."""
    out, t = [], t_start
    while t <= t_end + 1e-9:
        out += b.tick(t)
        t += step
    return out


def gestures(events):
    return [e.gesture for e in events if isinstance(e, Gesture)]


# --- Defaults (canon for now): click only, fired on release ---

def test_default_click_fires_on_release_with_no_delay():
    b = button()
    out = b.down(0.0) + b.up(0.1)
    assert gestures(out) == ["click"]
    assert out[0].t == 0.1


def test_default_long_press_is_still_just_a_click():
    b = button()
    out = b.down(0.0) + run_until(b, 2.0, 0.0) + b.up(2.0)
    assert gestures(out) == ["click"]


def test_default_quick_double_press_is_two_clicks():
    b = button()
    out = b.down(0.0) + b.up(0.1) + b.down(0.2) + b.up(0.3)
    assert gestures(out) == ["click", "click"]


# --- Opt-in double-click ---

def test_double_enabled_short_press_gives_click_after_window():
    b = button(double=True)
    out = b.down(0.0) + b.up(0.1)
    assert out == []  # waiting to see if a second press comes
    out += run_until(b, 0.5, 0.1)
    assert gestures(out) == ["click"]


def test_double_enabled_two_presses_inside_300ms_give_one_double_and_no_click():
    b = button(double=True)
    out = b.down(0.0) + b.up(0.1) + b.down(0.3)
    assert gestures(out) == ["double"]
    out = b.up(0.4) + run_until(b, 2.0, 0.4)
    assert gestures(out) == []


def test_double_enabled_second_press_after_window_is_two_clicks():
    b = button(double=True)
    out = b.down(0.0) + b.up(0.1) + b.down(0.5) + b.up(0.6) + run_until(b, 1.2, 0.6)
    assert gestures(out) == ["click", "click"]


# --- Opt-in hold ---

def test_hold_enabled_700ms_press_gives_one_hold_and_nothing_on_release():
    b = button(hold=True)
    out = b.down(0.0) + run_until(b, 1.5, 0.0)
    assert gestures(out) == ["hold"]
    out = b.up(1.5) + run_until(b, 3.0, 1.5)
    assert gestures(out) == []


# --- Stuck fault (always on) ---

def test_stuck_press_raises_fault_once_and_clears_on_release():
    b = button()
    out = b.down(0.0) + run_until(b, 31.0, 0.0, step=0.05)
    faults = [e for e in out if isinstance(e, FaultChange)]
    assert [(f.fault, f.active) for f in faults] == [("reset_stuck", True)]
    assert 30.0 <= faults[0].t < 30.1
    assert gestures(out) == []
    out = b.up(40.0) + run_until(b, 41.0, 40.0)
    assert [(e.fault, e.active) for e in out if isinstance(e, FaultChange)] == [("reset_stuck", False)]
    assert gestures(out) == []  # the stuck press doesn't turn into a click


def test_button_works_normally_after_a_stuck_fault_clears():
    b = button()
    b.down(0.0)
    run_until(b, 31.0, 0.0, step=0.05)
    b.up(31.0)
    out = b.down(32.0) + b.up(32.1)
    assert gestures(out) == ["click"]


def test_start_stuck_raises_fault_immediately_and_clears_on_release():
    b = button()
    out = b.start_stuck(5.0)
    assert [(e.fault, e.active) for e in out] == [("reset_stuck", True)]
    assert gestures(run_until(b, 7.0, 5.0)) == []
    out = b.up(8.0)
    assert [(e.fault, e.active) for e in out] == [("reset_stuck", False)]


def test_consumed_press_produces_nothing():
    b = button(hold=True)
    b.down(0.0)
    b.consume()
    out = run_until(b, 1.0, 0.0) + b.up(1.0) + run_until(b, 2.0, 1.0)
    assert gestures(out) == []
