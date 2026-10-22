"""The bus for the page builder's preview, in the owner's browser.

The broker only listens on the jukebox itself (Phase 8: nobody else on the
network can reach it), so the preview, running in a browser elsewhere,
reaches it through the admin: behind the login, from the admin's own pages
only, and one way. The preview only listens (bus.js never publishes in
preview mode), so every PUBLISH the browser sends is dropped here; the
rest (connect, subscribe, pings) goes through.
"""

import asyncio

import aiohttp
from aiohttp import web

PUBLISH = 3   # MQTT packet type (the first byte's high four bits)


class Packets:
    """Splits a stream of MQTT bytes into whole packets (a WebSocket frame
    may hold several, or part of one)."""

    def __init__(self):
        self.buf = b""

    def feed(self, data: bytes) -> list[bytes]:
        self.buf += data
        out = []
        while True:
            size = self._size()
            if size is None or len(self.buf) < size:
                return out
            out.append(self.buf[:size])
            self.buf = self.buf[size:]

    def _size(self) -> int | None:
        """The whole first packet's length, once its header is in."""
        length, mult = 0, 1
        for i in range(1, 5):          # the remaining length: 1 to 4 bytes after the type
            if len(self.buf) <= i:
                return None
            b = self.buf[i]
            length += (b & 0x7F) * mult
            if not b & 0x80:
                return 1 + i + length
            mult *= 128
        raise ValueError("not MQTT")


def listening_only(packets: list[bytes]) -> bytes:
    return b"".join(p for p in packets if p[0] >> 4 != PUBLISH)


def same_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    return origin is None or origin.split("://", 1)[-1] == request.host


async def relay(request: web.Request, broker: str) -> web.WebSocketResponse:
    """`broker`: the broker's local WebSocket address (ws://127.0.0.1:9001)."""
    if not same_origin(request):
        raise web.HTTPForbidden(text="Not from the admin's own pages.")
    browser = web.WebSocketResponse(protocols=("mqtt",), heartbeat=30)
    await browser.prepare(request)
    async with aiohttp.ClientSession() as session:
        try:
            upstream = await session.ws_connect(broker, protocols=("mqtt",))
        except aiohttp.ClientError:
            await browser.close(code=1011, message=b"The bus isn't answering.")
            return browser

        async def down():
            async for msg in upstream:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    await browser.send_bytes(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    await browser.send_str(msg.data)
            await browser.close()

        task = asyncio.create_task(down())
        packets = Packets()
        try:
            async for msg in browser:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    try:
                        data = listening_only(packets.feed(msg.data))
                    except ValueError:
                        break
                    if data:
                        await upstream.send_bytes(data)
        finally:
            task.cancel()
            await upstream.close()
    return browser
