"""jb-input: the Pico keypad's raw key events -> named keys and gestures on the
bus (jukebox/jb-input/*). Key-to-pin mapping: "Keyboard Input Build Brief.md".

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
from services.jb_input.device import open_keypad
from services.jb_input.pipeline import Pipeline
from services.jb_input.publisher import Publisher
from services.jb_input.runner import Runner


async def main(config_path=None) -> None:
    log = jb_logging.setup(config.SERVICE_NAME)
    settings = config.load(config_path)
    log.info("starting: device %r, grab=%s, extra gestures %s, push_turn=%s, actions %s",
             settings.device_name, settings.grab, settings.gestures, settings.push_turn,
             {f"{c}.{g}": a for (c, g), a in settings.actions.items()} or "none")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with Bus(config.SERVICE_NAME, host=settings.mqtt_host, port=settings.mqtt_port) as bus:
        heartbeat = asyncio.create_task(bus.run_heartbeat())
        publisher = Publisher(bus, settings.publish_raw)
        pipeline = Pipeline(settings)
        runner = Runner(settings, pipeline, publisher, lambda: open_keypad(settings), log)
        task = asyncio.create_task(runner.run())
        await stop.wait()
        for t in (task, heartbeat):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await publisher.status(pipeline.faults, time.monotonic(), online=False)
        log.info("stopped")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    asyncio.run(main(ap.parse_args().config))
