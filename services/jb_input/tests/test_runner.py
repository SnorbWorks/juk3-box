"""Device handling with a fake keypad: disconnect then reconnect clears held
state and updates status."""

import asyncio
import logging

from services.jb_input.config import Settings
from services.jb_input.device import DeviceLost
from services.jb_input.events import RawEvent
from services.jb_input.pipeline import Pipeline
from services.jb_input.runner import Runner


class FakeDevice:
    name, path, grabbed = "Jukebox JB Keypad", "/dev/input/fake", True

    def __init__(self, events, held=()):
        self.script = events
        self.held = list(held)
        self.closed = False

    def active_key_names(self):
        return self.held

    async def events(self, clock):
        for code, value in self.script:
            await asyncio.sleep(0)
            yield RawEvent(code, value, clock())
        raise DeviceLost("unplugged")

    def close(self):
        self.closed = True


class FakePublisher:
    def __init__(self):
        self.device = "missing"
        self.statuses = []   # (device, faults)
        self.published = []

    async def status(self, faults, t, online=True):
        self.statuses.append((self.device, sorted(faults)))

    async def events(self, events, faults, t):
        self.published += events


async def fake_sleep(_seconds):
    await asyncio.sleep(0)


def run(devices):
    settings = Settings()
    pipeline = Pipeline(settings)
    publisher = FakePublisher()
    queue = list(devices)
    clock = iter(range(10_000)).__next__  # strictly increasing fake time
    runner = Runner(settings, pipeline, publisher, lambda: queue.pop(0) if queue else None,
                    logging.getLogger("test"), clock=lambda: float(clock()), sleep=fake_sleep)
    asyncio.run(runner.run(sessions=sum(d is not None for d in devices)))
    return pipeline, publisher


def test_disconnect_then_reconnect_clears_held_state_and_updates_status():
    # Session 1: knob push goes down and the device vanishes while it's held.
    first = FakeDevice([("KEY_ENTER", 1)])
    second = FakeDevice([("KEY_B", 1), ("KEY_B", 0)])
    pipeline, publisher = run([None, first, second])
    assert first.closed and second.closed
    assert [s[0] for s in publisher.statuses] == ["missing", "connected", "missing", "connected", "missing"]
    # The push that was held when the device vanished was forgotten.
    assert not pipeline.buttons["knob.push"].is_down


def test_reset_held_at_connect_raises_reset_stuck():
    dev = FakeDevice([], held=["KEY_ESC"])
    pipeline, publisher = run([dev])
    assert ("connected", ["reset_stuck"]) in publisher.statuses
    # Disconnecting clears it (it's re-raised on reconnect if still held).
    assert publisher.statuses[-1] == ("missing", [])
