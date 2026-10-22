"""Device handling with fake devices: disconnect then reconnect clears held
state and updates status; keyboards work beside the keypad; learning."""

import asyncio
import logging

from services.jb_input.config import Settings
from services.jb_input.device import DeviceLost
from services.jb_input.events import Gesture, KeyPress, RawEvent
from services.jb_input.pipeline import Pipeline
from services.jb_input.runner import Runner


class FakeDevice:
    grabbed = True

    def __init__(self, events, held=(), path="/dev/input/fake", name="Jukebox JB Keypad", stays=False):
        self.script = events
        self.stays = stays   # plugged in until the test ends
        self.held = list(held)
        self.closed = False
        self.path, self.name, self.id = path, name, f"239a:80f4:{name}"

    def active_key_names(self):
        return self.held

    async def events(self, clock):
        for code, value in self.script:
            await asyncio.sleep(0)
            yield RawEvent(code, value, clock())
        if self.stays:
            await asyncio.Event().wait()
        raise DeviceLost("unplugged")

    def close(self):
        self.closed = True


class FakePublisher:
    def __init__(self):
        self.device, self.keyboards, self.learning, self.keypad = "missing", 0, False, None
        self.statuses = []   # (device, faults)
        self.published = []
        self.learned = []

    async def status(self, faults, t, online=True):
        self.statuses.append((self.device, sorted(faults)))

    async def events(self, events, faults, t):
        self.published += events

    async def learn(self, dev, role, raw):
        self.learned.append((dev.name, role, raw.code, raw.value))


async def fake_sleep(_seconds):
    await asyncio.sleep(0)


def make_runner(scans, settings=None):
    """scans: what each look for devices finds, in turn: a list of
    (device, role), or None/[] for nothing."""
    publisher = FakePublisher()
    queue = list(scans)
    pipelines = []

    def make_pipeline(settings, role):
        pipelines.append(Pipeline(settings, role))
        return pipelines[-1]

    clock = iter(range(10_000)).__next__  # strictly increasing fake time
    runner = Runner(settings or Settings(), make_pipeline, publisher,
                    lambda settings, open_paths, learning: queue.pop(0) or [] if queue else [],
                    logging.getLogger("test"), clock=lambda: float(clock()), sleep=fake_sleep)
    return runner, pipelines, publisher


def run(scans, sessions):
    runner, pipelines, publisher = make_runner(scans)
    asyncio.run(runner.run(sessions=sessions))
    return pipelines, publisher


def test_disconnect_then_reconnect_clears_held_state_and_updates_status():
    # Session 1: knob push goes down and the device vanishes while it's held.
    first = FakeDevice([("KEY_ENTER", 1)])
    second = FakeDevice([("KEY_B", 1), ("KEY_B", 0)], path="/dev/input/event9")
    pipelines, publisher = run([None, [(first, "keypad")], [(second, "keypad")]], sessions=2)
    assert first.closed and second.closed
    devices = [s[0] for s in publisher.statuses]
    assert devices[0] == "missing" and devices[-1] == "missing"
    assert devices.count("connected") >= 2
    # The push that was held when the device vanished was forgotten.
    assert not pipelines[0].buttons["knob.push"].is_down


def test_reset_held_at_connect_raises_reset_stuck():
    dev = FakeDevice([], held=["KEY_ESC"])
    _, publisher = run([[(dev, "keypad")]], sessions=1)
    assert ("connected", ["reset_stuck"]) in publisher.statuses
    # Disconnecting clears it (it's re-raised on reconnect if still held).
    assert publisher.statuses[-1] == ("missing", [])


def test_a_keyboard_works_beside_the_keypad():
    keypad = FakeDevice([("KEY_B", 1), ("KEY_B", 0)])
    keyboard = FakeDevice([("KEY_7", 1), ("KEY_7", 0), ("KEY_UP", 1), ("KEY_UP", 0)],
                          path="/dev/input/kbd", name="USB Keyboard")
    _, publisher = run([[(keypad, "keypad"), (keyboard, "keyboard")]], sessions=2)
    keys = [e.key for e in publisher.published if isinstance(e, KeyPress)]
    assert sorted(keys) == ["7", "B"]
    assert [(g.control, g.steps) for g in publisher.published if isinstance(g, Gesture)] == [("volume", 1)]


def test_learning_reports_presses_instead_of_using_them():
    async def go():
        dev = FakeDevice([("KEY_Q", 1), ("KEY_Q", 0)], name="Arcade Board")
        runner, _, publisher = make_runner([[], [(dev, "other")]])
        await runner.set_learning(True)
        assert publisher.learning
        await runner.run(sessions=1)
        return publisher
    publisher = asyncio.run(go())
    assert publisher.learned == [("Arcade Board", "other", "KEY_Q", 1), ("Arcade Board", "other", "KEY_Q", 0)]
    assert not [e for e in publisher.published if isinstance(e, KeyPress)]


def test_learning_ends_by_itself():
    async def go():
        runner, _, publisher = make_runner([])
        await runner.set_learning(True)
        runner.learn_until = runner.clock()   # time's up
        ticker = asyncio.create_task(runner._tick_loop())
        for _ in range(5):
            await asyncio.sleep(0)
        ticker.cancel()
        return runner, publisher
    runner, publisher = asyncio.run(go())
    assert not runner.learning and not publisher.learning


def test_our_keypad_is_asked_into_maintenance_mode_and_keyboards_are_not():
    async def go():
        signalled = []
        keypad = FakeDevice([], name="Jukebox JB Keypad 2.0", stays=True)
        keyboard = FakeDevice([], path="/dev/input/kbd", name="USB Keyboard", stays=True)
        for d in (keypad, keyboard):
            async def signal(d=d):
                signalled.append(d.name)
            d.signal = signal
        runner, _, publisher = make_runner([])
        runner._start(keypad, "keypad")
        runner._start(keyboard, "keyboard")
        await asyncio.sleep(0)
        assert publisher.keypad == "Jukebox JB Keypad 2.0"
        assert await runner.keypad_maintenance() == 1
        for *_, task in list(runner.sessions.values()):
            task.cancel()
        return signalled
    assert asyncio.run(go()) == ["Jukebox JB Keypad 2.0"]


def test_the_signal_matches_the_keypad_programs():
    import importlib.util
    from pathlib import Path
    from services.jb_input.device import MAINTENANCE_SIGNAL
    path = Path(__file__).resolve().parents[3] / "firmware" / "pico-keypad" / "jbsignal.py"
    spec = importlib.util.spec_from_file_location("jbsignal", path)
    jbsignal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jbsignal)
    assert jbsignal.PATTERN == MAINTENANCE_SIGNAL
    s = jbsignal.Signal()
    assert not any(s.feed(v, 10 + i * 0.08) for i, v in enumerate((0, 1, 2, 4, 2, 1, 7)))
    assert s.feed(0, 11)                                    # the last one completes it
    s = jbsignal.Signal()
    assert not any(s.feed(v, 20 + i * 1.0) for i, v in enumerate(MAINTENANCE_SIGNAL))   # too slow
