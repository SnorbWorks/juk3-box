"""jb-source-helper: the juk3box Helper iPhone app as a music source.

The app plays the owner's Apple Music library with MusicKit and sends the
sound to the cabinet over AirPlay; this service is its other half
(docs/contracts/helper-protocol.md):

- announces the jukebox over Bonjour (_juk3box._tcp, port 8770) and serves
  the app's WebSocket (/helper) and artwork uploads (/artwork/<key>);
- pairs a phone once with a code on the cabinet's screen;
- publishes the app's library, retained, for jb-library, and plays picks
  from jb-core ("helper:<device>/<item>") on the app;
- wakes the app over Bluetooth LE when it's asleep (ble.py), but only once
  a phone is paired.

Beside Bluetooth (jb-source-phone), not instead of it.
"""

import asyncio
import hashlib
from pathlib import Path

from aiohttp import web

from shared import config, logging as jb_logging
from shared.bus import Bus
from services.jb_source_helper import avahi, protocol
from services.jb_source_helper.ble import Wake
from services.jb_source_helper.phones import Phones
from services.jb_source_helper.source import TOPIC, Helper

SERVICE_NAME = "jb-source-helper"
DEFAULTS = {
    "port": 8770,
    "jukebox_name": "JUKE.BOX",
    "data_dir": "/var/lib/juk3-box/helper",
    "ble": True,
}
MAX_ARTWORK = 2 * 1024 * 1024


def jukebox_id(machine_id: Path = Path("/etc/machine-id")) -> str:
    try:
        seed = machine_id.read_text().strip()
    except OSError:
        seed = "juk3-box"
    return hashlib.sha1(f"juk3box-helper:{seed}".encode()).hexdigest()[:16]


def make_app(helper: Helper) -> web.Application:
    async def websocket(request):
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        try:
            await helper.serve(ws)
        finally:
            await ws.close()
        return ws

    async def artwork(request):
        key = request.match_info["key"]
        if not protocol.ARTWORK_KEY_RE.match(key):
            raise web.HTTPBadRequest()
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not helper.phones.device_for_token(auth[7:].strip()):
            raise web.HTTPUnauthorized()
        if (request.content_length or 0) > MAX_ARTWORK:
            raise web.HTTPRequestEntityTooLarge(MAX_ARTWORK, request.content_length)
        body = await request.content.read(MAX_ARTWORK + 1)
        if len(body) > MAX_ARTWORK:
            raise web.HTTPRequestEntityTooLarge(MAX_ARTWORK, len(body))
        if not body.startswith(b"\xff\xd8"):
            raise web.HTTPBadRequest(text="not a JPEG")
        await asyncio.to_thread(helper.save_artwork, key, body)
        return web.Response(status=204)

    app = web.Application(client_max_size=MAX_ARTWORK + 1024)
    app.router.add_get("/helper", websocket)
    app.router.add_post("/artwork/{key}", artwork)
    return app


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = {**DEFAULTS, **config.load(SERVICE_NAME)}
    data_dir = Path(cfg["data_dir"])
    jid = jukebox_id()
    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        phones = Phones(data_dir / "phones.json")
        ble = Wake(jid, log) if cfg["ble"] else None
        helper = Helper(bus, phones, data_dir, log, jid, cfg["jukebox_name"], ble)
        commands = await bus.subscribe(f"{TOPIC}/cmd")
        asyncio.create_task(bus.run_heartbeat())
        if ble:
            await ble.ensure_running(bool(phones.phones))
        await helper.start()

        runner = web.AppRunner(make_app(helper), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", cfg["port"]).start()
        announcement = avahi.Announcement(log)
        await announcement.start(cfg["jukebox_name"], cfg["port"], avahi.txt(jid, cfg["jukebox_name"]))
        log.info("listening on port %d", cfg["port"])

        async def run(cmd):
            try:
                await helper.command(cmd)
            except Exception:
                log.exception("failed to handle command: %r", cmd)

        try:
            async for _topic, payload in commands:
                # A pick can wait for a sleeping app: never hold up the next command.
                asyncio.create_task(run(payload))
        finally:
            await announcement.stop()
            if ble:
                await ble.stop()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
