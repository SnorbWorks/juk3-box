"""The bus client rides out the broker going away (mosquitto restarting, or
dropping everyone when the clock jumps at boot)."""

import asyncio
import json
from types import SimpleNamespace

import aiomqtt
import pytest

from shared import bus as bus_mod


class Broker:
    """Clients connect while it's up; dropping it ends every connection."""

    def __init__(self):
        self.up = True
        self.clients = []
        self.published = []


def fake_client(broker):
    class Client:
        def __init__(self, *_a, **_k):
            self.topics = []
            self.queue = asyncio.Queue()
            self.gone = False

        async def __aenter__(self):
            if not broker.up:
                raise aiomqtt.MqttError("refused")
            broker.clients.append(self)
            return self

        async def __aexit__(self, *_):
            self.gone = True

        async def subscribe(self, topic):
            self._alive()
            self.topics.append(topic)

        async def publish(self, topic, payload, retain=False):
            self._alive()
            broker.published.append((topic, payload))

        def _alive(self):
            if self.gone or not broker.up:
                raise aiomqtt.MqttError("disconnected")

        def drop(self):
            self.queue.put_nowait(None)

        @property
        def messages(self):
            async def gen():
                while True:
                    m = await self.queue.get()
                    if m is None:
                        raise aiomqtt.MqttError("Disconnected during message iteration")
                    yield m
            return gen()

        def deliver(self, topic, payload):
            self.queue.put_nowait(SimpleNamespace(topic=SimpleNamespace(value=topic), payload=json.dumps(payload).encode()))
    return Client


@pytest.fixture
def broker(monkeypatch):
    b = Broker()
    monkeypatch.setattr(bus_mod.aiomqtt, "Client", fake_client(b))
    monkeypatch.setattr(bus_mod, "RETRY_S", (0.01,))
    return b


def test_it_reconnects_and_subscribes_again(broker):
    async def go():
        async with bus_mod.Bus("jb-test") as bus:
            messages = await bus.subscribe("jukebox/a")
            first = broker.clients[-1]
            first.deliver("jukebox/a", {"n": 1})
            assert await anext(messages) == ("jukebox/a", {"n": 1})
            broker.up = False                   # mosquitto restarts
            first.drop()
            got = asyncio.create_task(anext(messages))
            await asyncio.sleep(0.05)
            assert not got.done() and len(broker.clients) == 1
            broker.up = True
            await asyncio.sleep(0.05)
            second = broker.clients[-1]
            assert second is not first and second.topics == ["jukebox/a"]
            second.deliver("jukebox/a", {"n": 2})
            assert await got == ("jukebox/a", {"n": 2})
    asyncio.run(go())


def test_publishing_waits_for_the_broker(broker):
    async def go():
        async with bus_mod.Bus("jb-test") as bus:
            broker.up = False
            pending = asyncio.create_task(bus.publish("jukebox/x", {"v": 1}))
            await asyncio.sleep(0.05)
            assert not pending.done()
            broker.up = True
            await asyncio.wait_for(pending, 1)
            assert broker.published == [("jukebox/x", '{"v": 1}')]
            assert len(broker.clients) == 2      # one new connection
    asyncio.run(go())


def test_it_waits_for_a_broker_that_isnt_up_yet(broker):
    async def go():
        broker.up = False
        entering = asyncio.create_task(bus_mod.Bus("jb-test").__aenter__())
        await asyncio.sleep(0.05)
        assert not entering.done()
        broker.up = True
        assert isinstance(await asyncio.wait_for(entering, 1), bus_mod.Bus)
    asyncio.run(go())
