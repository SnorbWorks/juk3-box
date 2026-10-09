import asyncio

from shared import config, logging as jb_logging
from shared.bus import Bus

SERVICE_NAME = "jb-audio"
SINK = "@DEFAULT_AUDIO_SINK@"


async def run_wpctl(*args):
    proc = await asyncio.create_subprocess_exec(
        "wpctl", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"wpctl {args} failed: {err.decode().strip()}")
    return out.decode().strip()


async def get_state() -> dict:
    raw = await run_wpctl("get-volume", SINK)
    # e.g. "Volume: 0.25" or "Volume: 0.25 [MUTED]"
    parts = raw.split()
    return {"volume": float(parts[1]), "muted": "[MUTED]" in raw}


async def publish_state(bus: Bus, log) -> None:
    state = await get_state()
    # Retained, so a screen that loads later still knows the volume.
    await bus.publish(f"jukebox/{SERVICE_NAME}/state", state, retain=True)
    log.info("state: %s", state)


async def handle_command(bus: Bus, log, cmd: dict) -> None:
    action = cmd.get("action")
    if action == "volume_delta":
        delta = cmd.get("value", 0)
        sign = "+" if delta >= 0 else "-"
        # wpctl allows over-amplification above 100% by default -- cap it,
        # both to keep the UI's bar meaningful and to avoid clipping/distortion.
        await run_wpctl("set-volume", "-l", "1.0", SINK, f"{abs(delta) * 100:.0f}%{sign}")
    elif action == "mute":
        await run_wpctl("set-mute", SINK, "1")
    elif action == "unmute":
        await run_wpctl("set-mute", SINK, "0")
    elif action == "toggle_mute":
        await run_wpctl("set-mute", SINK, "toggle")
    else:
        log.warning("unknown command action: %r", action)
        return
    await publish_state(bus, log)


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        await publish_state(bus, log)
        asyncio.create_task(bus.run_heartbeat())
        async for _topic, payload in await bus.subscribe(f"jukebox/{SERVICE_NAME}/cmd"):
            try:
                await handle_command(bus, log, payload)
            except Exception:
                log.exception("failed to handle command: %r", payload)


if __name__ == "__main__":
    asyncio.run(main())
