"""Connects the devices, their pure pipelines and the publisher, and handles
devices disappearing and coming back. Every keypad and keyboard plugged in
works at once, each through its own pipeline (its keymap, its gestures in
progress). The device scanner, publisher and clock are injected, so tests can
run it against fake devices.

Learning (the setup wizard's controls step asks, jukebox/jb-input/cmd): every
key event of every device goes out on jukebox/jb-input/learn instead of
working as usual, so the wizard sees what each button sends. It ends when
asked, or by itself after LEARN_S so a closed wizard can't leave the jukebox
deaf.
"""

import asyncio
import time

from services.jb_input.device import DeviceLost

TICK_S = 0.01   # how often pending timers (hold, double-click, coalescing, stuck, settle) are checked
LEARN_S = 300


class Runner:
    def __init__(self, settings, make_pipeline, publisher, scan, log, clock=time.monotonic,
                 sleep=asyncio.sleep):
        self.settings = settings
        self.make_pipeline = make_pipeline   # (settings, role) -> Pipeline
        self.publisher = publisher
        self.scan = scan                     # (settings, open paths, learning) -> [(device, role)]
        self.log = log
        self.clock = clock
        self.sleep = sleep
        self.sessions: dict[str, tuple] = {}   # path -> (device, role, pipeline, task)
        self.learn_until = None
        self.ended = 0

    @property
    def learning(self) -> bool:
        return self.learn_until is not None

    @property
    def faults(self) -> set:
        return set().union(*(p.faults for _, _, p, _ in self.sessions.values()))

    def _count(self, role) -> int:
        return sum(r == role for _, r, _, _ in self.sessions.values())

    async def _status(self, t):
        self.publisher.device = "connected" if self._count("keypad") else "missing"
        self.publisher.keyboards = self._count("keyboard")
        self.publisher.learning = self.learning
        await self.publisher.status(self.faults, t)

    async def run(self, sessions: int | None = None):
        """Runs forever (or until `sessions` devices have come and gone, in tests)."""
        await self._status(self.clock())
        ticker = asyncio.create_task(self._tick_loop())
        try:
            missing_logged = False
            while sessions is None or self.ended < sessions:
                for dev, role in self.scan(self.settings, set(self.sessions), self.learning):
                    self._start(dev, role)
                if self._count("keypad"):
                    missing_logged = False
                elif not missing_logged:
                    self.log.warning("keypad %r not found, looking every %ss",
                                     self.settings.device_name, self.settings.reconnect_s)
                    missing_logged = True
                await self.sleep(self.settings.reconnect_s)
        finally:
            ticker.cancel()
            for *_, task in list(self.sessions.values()):
                task.cancel()

    def _start(self, dev, role):
        pipeline = self.make_pipeline(self.settings, role)
        task = asyncio.create_task(self._session(dev, role, pipeline))
        self.sessions[dev.path] = (dev, role, pipeline, task)

    async def _session(self, dev, role, pipeline):
        t = self.clock()
        self.log.info("%s connected: %s (%s)%s", role, dev.name, dev.path, ", grabbed" if dev.grabbed else "")
        # Clear any state from before the disconnect, then honour keys already held.
        events = pipeline.reset_state(t) + pipeline.start_held(dev.active_key_names(), t)
        await self._status(t)
        await self.publisher.events(events, self.faults, t)
        try:
            async for raw in dev.events(self.clock):
                if self.learning:
                    if raw.value in (0, 1):
                        await self.publisher.learn(dev, role, raw)
                    continue
                await self.publisher.events(pipeline.feed(raw), self.faults, raw.t)
        except DeviceLost as e:
            self.log.warning("%s lost: %s", role, e)
        finally:
            dev.close()
            if self.sessions.get(dev.path, (None,))[0] is dev:
                del self.sessions[dev.path]
            self.ended += 1
            t = self.clock()
            events = pipeline.reset_state(t)
            await self._status(t)
            await self.publisher.events(events, self.faults, t)

    async def set_learning(self, on: bool):
        """Start or stop learning. Starting looks for devices at once (an
        arcade board that only opens while learning); stopping closes those
        and forgets whatever was half-pressed meanwhile."""
        t = self.clock()
        if on:
            self.learn_until = t + LEARN_S
            for dev, role in self.scan(self.settings, set(self.sessions), True):
                self._start(dev, role)
        elif self.learning:
            self.learn_until = None
            for dev, role, pipeline, task in list(self.sessions.values()):
                if role == "other":
                    task.cancel()
                else:
                    await self.publisher.events(pipeline.reset_state(t), self.faults, t)
        self.log.info("learning %s", "on" if on else "off")
        await self._status(t)

    async def reload(self, settings):
        """New settings (the wizard saved what it learned): close every device;
        the next scan opens them again with their new roles and keymaps."""
        self.settings = settings
        tasks = [task for *_, task in self.sessions.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.log.info("controls reloaded: %d keypad(s) known, knob %d step(s) a click",
                      len(settings.keypads), settings.knob_steps_per_click)

    async def _tick_loop(self):
        while True:
            t = self.clock()
            if self.learn_until is not None and t >= self.learn_until:
                await self.set_learning(False)
            for _, _, pipeline, _ in list(self.sessions.values()):
                await self.publisher.events(pipeline.tick(t), self.faults, t)
            await self.sleep(TICK_S)
