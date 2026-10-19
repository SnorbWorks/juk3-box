import asyncio

from shared import config, logging as jb_logging, pipewire as pw
from shared.bus import Bus
from services.jb_audio.airplay import Receivers
from services.jb_audio.outputs import Outputs, Settings

SERVICE_NAME = "jb-audio"
# Volume is the DAC's, looked up by name: with whole-home on, the default
# sink is jb-whole-home (a delay in front of the DAC), not the DAC itself.
FALLBACK_SINK = "@DEFAULT_AUDIO_SINK@"
SOURCES = {
    "jukebox/jb-source-phone/now-playing": "phone",
    "jukebox/jb-source-local/now-playing": "local",
}
PHONE_CONNECTED = "jukebox/jb-source-phone/connected"

_dac_id: str | None = None


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


async def volume_sink(refresh: bool = False) -> str:
    global _dac_id
    if _dac_id is None or refresh:
        try:
            dac = pw.find_dac(await pw.dump())
        except Exception:
            dac = None
        _dac_id = str(dac["id"]) if dac else None
    return _dac_id or FALLBACK_SINK


async def wpctl_volume(*args):
    """A wpctl volume call on the DAC; ids change when PipeWire restarts, so
    one failure re-looks the DAC up and tries again."""
    sink = await volume_sink()
    try:
        return await run_wpctl(*[sink if a is None else a for a in args])
    except RuntimeError:
        sink = await volume_sink(refresh=True)
        return await run_wpctl(*[sink if a is None else a for a in args])


async def get_state() -> dict:
    raw = await wpctl_volume("get-volume", None)
    # e.g. "Volume: 0.25" or "Volume: 0.25 [MUTED]"
    parts = raw.split()
    return {"volume": float(parts[1]), "muted": "[MUTED]" in raw}


async def publish_state(bus: Bus, log, outputs: Outputs) -> None:
    state = {**await get_state(), "whole_home": outputs.status()}
    # Retained, so a screen that loads later still knows the volume.
    await bus.publish(f"jukebox/{SERVICE_NAME}/state", state, retain=True)
    log.info("state: %s", state)


async def handle_command(bus: Bus, log, outputs: Outputs, cmd: dict) -> None:
    action = cmd.get("action")
    if action == "volume_delta":
        delta = cmd.get("value", 0)
        sign = "+" if delta >= 0 else "-"
        # wpctl allows over-amplification above 100% by default -- cap it,
        # both to keep the UI's bar meaningful and to avoid clipping/distortion.
        await wpctl_volume("set-volume", "-l", "1.0", None, f"{abs(delta) * 100:.0f}%{sign}")
    elif action == "mute":
        await wpctl_volume("set-mute", None, "1")
    elif action == "unmute":
        await wpctl_volume("set-mute", None, "0")
    elif action == "toggle_mute":
        await wpctl_volume("set-mute", None, "toggle")
    elif action == "whole_home":
        # {"action": "whole_home", "enabled": true, "airplay": "Inside"}
        await outputs.set_enabled(bool(cmd.get("enabled")), cmd.get("airplay"))
    elif action == "whole_home_offset":
        # Fine-tuning the sync by ear, on top of the measured delay:
        # {"action": "whole_home_offset", "ms": 20} (positive holds the cabinet back more)
        await outputs.set_offset(int(cmd["ms"]))
    elif action == "whole_home_resync":
        await outputs.resync()
    elif action == "airplay_volume":
        await outputs.set_airplay_volume(float(cmd["value"]))
    else:
        log.warning("unknown command action: %r", action)
        return
    await publish_state(bus, log, outputs)


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])

        async def outputs_changed(_status):
            await publish_state(bus, log, outputs)

        outputs = Outputs(Settings.load(), on_change=outputs_changed)

        async def receivers_changed(names):
            # Retained: the admin's Sound page lists them to pick a zone from.
            await bus.publish(f"jukebox/{SERVICE_NAME}/airplay", {"receivers": names}, retain=True)

        receivers = Receivers(on_change=receivers_changed)
        cmd_topic = f"jukebox/{SERVICE_NAME}/cmd"
        # One subscription per topic, read through one iterator (they share
        # the client's message queue).
        messages = await bus.subscribe(cmd_topic)
        for topic in (*SOURCES, PHONE_CONNECTED):
            await bus.subscribe(topic)
        await publish_state(bus, log, outputs)
        tasks = [asyncio.create_task(bus.run_heartbeat()), asyncio.create_task(outputs.run()),
                 asyncio.create_task(receivers.run())]
        try:
            async for topic, payload in messages:
                try:
                    if topic == cmd_topic:
                        await handle_command(bus, log, outputs, payload)
                    elif topic in SOURCES:
                        if outputs.source_status(SOURCES[topic], payload.get("status")):
                            await outputs.tick()   # start the feed now, not on the next tick
                    elif topic == PHONE_CONNECTED and not payload.get("connected"):
                        outputs.source_status("phone", None)
                except Exception:
                    log.exception("failed to handle %s: %r", topic, payload)
        finally:
            for t in tasks:
                t.cancel()
            await outputs.stop()


if __name__ == "__main__":
    asyncio.run(main())
