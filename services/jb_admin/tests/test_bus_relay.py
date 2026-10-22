"""The preview's bus through the admin: whole packets, and listening only."""

import asyncio

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.jb_admin import bus_relay

CONNECT = bytes([0x10, 2]) + b"ab"
SUBSCRIBE = bytes([0x82, 3]) + b"xyz"
PUBLISH = bytes([0x30, 4]) + b"pubs"
BIG = bytes([0x30, 0x80, 0x01]) + b"x" * 128        # 128 bytes: a two-byte length


def test_packets_are_split_however_they_arrive():
    p = bus_relay.Packets()
    stream = CONNECT + BIG + SUBSCRIBE
    got = []
    for i in range(0, len(stream), 5):
        got += p.feed(stream[i:i + 5])
    assert got == [CONNECT, BIG, SUBSCRIBE] and p.buf == b""


def test_the_browser_can_listen_but_not_publish():
    p = bus_relay.Packets()
    assert bus_relay.listening_only(p.feed(CONNECT + PUBLISH + SUBSCRIBE + BIG)) == CONNECT + SUBSCRIBE


def test_the_relay_carries_both_ways_and_drops_publishes():
    async def go():
        heard = []

        async def broker(request):
            ws = web.WebSocketResponse(protocols=("mqtt",))
            await ws.prepare(request)
            async for msg in ws:
                heard.append(msg.data)
                await ws.send_bytes(b"\x90\x03ack")      # a SUBACK-ish answer
            return ws

        b = web.Application()
        b.router.add_get("/", broker)
        async with TestServer(b) as bs:
            app = web.Application()
            app.router.add_get("/bus", lambda r: bus_relay.relay(r, f"ws://{bs.host}:{bs.port}/"))
            async with TestClient(TestServer(app)) as c:
                ws = await c.ws_connect("/bus", protocols=("mqtt",))
                await ws.send_bytes(PUBLISH + SUBSCRIBE)
                msg = await asyncio.wait_for(ws.receive(), 2)
                assert msg.data == b"\x90\x03ack"
                await ws.close()
                r = await c.get("/bus", headers={"Origin": "http://evil.example", "Connection": "Upgrade",
                                                 "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
                                                 "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
                assert r.status == 403
        assert heard == [SUBSCRIBE]
    asyncio.run(go())
