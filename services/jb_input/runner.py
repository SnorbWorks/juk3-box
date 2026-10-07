"""Connects the device, the pure pipeline and the publisher, and handles the
keypad disappearing and coming back. The device opener, publisher and clock
are injected, so tests can run it against a fake device.
"""

import asyncio
import time

from services.jb_input.device import DeviceLost

TICK_S = 0.01  # how often pending timers (hold, double-click, coalescing, stuck) are checked


class Runner:
    def __init__(self, settings, pipeline, publisher, open_device, log, clock=time.monotonic,
                 sleep=asyncio.sleep):
        self.settings = settings
        self.pipeline = pipeline
        self.publisher = publisher
        self.open_device = open_device
        self.log = log
        self.clock = clock
        self.sleep = sleep

    async def run(self, sessions: int | None = None):
        """Runs forever (or for `sessions` connect/disconnect cycles, in tests)."""
        self.publisher.device = "missing"
        await self.publisher.status(self.pipeline.faults, self.clock())
        ticker = asyncio.create_task(self._tick_loop())
        try:
            done = 0
            missing_logged = False
            while sessions is None or done < sessions:
                dev = self.open_device()
                if dev is None:
                    if not missing_logged:
                        self.log.warning("keypad %r not found, retrying every %ss",
                                         self.settings.device_name, self.settings.reconnect_s)
                        missing_logged = True
                    await self.sleep(self.settings.reconnect_s)
                    continue
                missing_logged = False
                await self._session(dev)
                done += 1
        finally:
            ticker.cancel()

    async def _session(self, dev):
        t = self.clock()
        self.log.info("keypad connected: %s (%s)%s", dev.name, dev.path, ", grabbed" if dev.grabbed else "")
        # Clear any state from before the disconnect, then honour keys already held.
        events = self.pipeline.reset_state(t) + self.pipeline.start_held(dev.active_key_names(), t)
        self.publisher.device = "connected"
        await self.publisher.status(self.pipeline.faults, t)
        await self.publisher.events(events, self.pipeline.faults, t)
        try:
            async for raw in dev.events(self.clock):
                await self.publisher.events(self.pipeline.feed(raw), self.pipeline.faults, raw.t)
        except DeviceLost as e:
            self.log.warning("keypad lost: %s", e)
        finally:
            dev.close()
            t = self.clock()
            events = self.pipeline.reset_state(t)
            self.publisher.device = "missing"
            await self.publisher.status(self.pipeline.faults, t)
            await self.publisher.events(events, self.pipeline.faults, t)

    async def _tick_loop(self):
        while True:
            t = self.clock()
            await self.publisher.events(self.pipeline.tick(t), self.pipeline.faults, t)
            await self.sleep(TICK_S)
