"""jb-input: the keypad's and any keyboard's raw key events -> named keys and
gestures on the bus (jukebox/jb-input/*). Key-to-pin mapping: "Keyboard Input
Build Brief.md"; what the setup wizard learned: config.load_controls.

Commands on jukebox/jb-input/cmd:
  {"action": "learn", "on": true|false}   the setup wizard's teaching (runner.py)
  {"action": "reload"}                    read the learned controls again (also
                                          on jukebox/jb-admin/changed "controls")

    python -m services.jb_input.main [--config /etc/juk3-box/jb-input.json]
"""

import argparse
import asyncio
import contextlib
import signal
import time

from shared import logging as jb_logging
from shared.bus import Bus
from services.jb_input import config
from services.jb_input.device import scan
from services.jb_input.pipeline import Pipeline
from services.jb_input.publisher import Publisher
from services.jb_input.runner import Runner


CMD = f"jukebox/{config.SERVICE_NAME}/cmd"
CHANGED = "jukebox/jb-admin/changed"


def load_settings(config_path, log):
    """The settings with the learned controls over them. A learned file that
    doesn't make sense is skipped (logged): the keypad's defaults still work."""
    settings = config.load(config_path)
    try:
        config.load_controls(settings)
    except config.ConfigError as e:
        log.warning("ignoring the learned controls: %s", e)
    return settings


async def listen(bus, runner, config_path, log):
    messages = await bus.subscribe(CMD)
    await bus.subscribe(CHANGED)
    async for topic, payload in messages:
        try:
            if topic == CMD and payload.get("action") == "learn":
                await runner.set_learning(bool(payload.get("on")))
            elif (topic == CMD and payload.get("action") == "reload") or (
                    topic == CHANGED and "controls" in (payload.get("what") or [])):
                await runner.reload(load_settings(config_path, log))
        except Exception:
            log.exception("failed to handle %s: %r", topic, payload)


async def main(config_path=None) -> None:
    log = jb_logging.setup(config.SERVICE_NAME)
    settings = load_settings(config_path, log)
    log.info("starting: device %r, grab=%s, extra gestures %s, push_turn=%s, actions %s",
             settings.device_name, settings.grab, settings.gestures, settings.push_turn,
             {f"{c}.{g}": a for (c, g), a in settings.actions.items()} or "none")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with Bus(config.SERVICE_NAME, host=settings.mqtt_host, port=settings.mqtt_port) as bus:
        publisher = Publisher(bus, settings.publish_raw)
        runner = Runner(settings, Pipeline, publisher, scan, log)
        workers = {
            asyncio.create_task(runner.run(), name="runner"),
            asyncio.create_task(listen(bus, runner, config_path, log), name="commands"),
            asyncio.create_task(bus.run_heartbeat(), name="heartbeat"),
        }
        stopping = asyncio.create_task(stop.wait(), name="stop")

        # Supervise: these tasks should run forever. If any one ends -- e.g.
        # the broker drops the connection, which it does to every client
        # when the clock jumps forward at boot (NTP after a power-off) --
        # exit non-zero so systemd restarts us cleanly. Previously a dead
        # task was ignored: the service stayed "active", kept the keypad
        # grabbed, and silently swallowed every key press.
        done, _ = await asyncio.wait(workers | {stopping}, return_when=asyncio.FIRST_COMPLETED)
        for t in workers | {stopping}:
            t.cancel()
        for t in workers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if stopping not in done:
            failed = next(iter(done))
            exc = None if failed.cancelled() else failed.exception()
            log.error("%s task ended unexpectedly (%r) -- exiting so systemd restarts jb-input",
                      failed.get_name(), exc)
            raise SystemExit(1)
        with contextlib.suppress(Exception):
            await publisher.status(runner.faults, time.monotonic(), online=False)
        log.info("stopped")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    asyncio.run(main(ap.parse_args().config))
