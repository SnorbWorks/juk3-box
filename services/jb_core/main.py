import asyncio

from shared import config, logging as jb_logging
from shared.bus import Bus

SERVICE_NAME = "jb-core"


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        await bus.run_heartbeat()


if __name__ == "__main__":
    asyncio.run(main())
