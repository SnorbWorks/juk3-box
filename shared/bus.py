"""MQTT bus client shared by every jb-* service."""

import asyncio
import json
import time

import aiomqtt


class Bus:
    """Wraps an aiomqtt client with JSON helpers and a heartbeat publisher."""

    def __init__(self, service_name: str, host: str = "localhost", port: int = 1883):
        self.service_name = service_name
        self.host = host
        self.port = port
        self._client: aiomqtt.Client | None = None

    async def __aenter__(self):
        self._client = aiomqtt.Client(self.host, port=self.port)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc_info):
        await self._client.__aexit__(*exc_info)

    async def publish(self, topic: str, payload: dict) -> None:
        await self._client.publish(topic, json.dumps(payload))

    async def subscribe(self, topic: str):
        await self._client.subscribe(topic)
        async for message in self._client.messages:
            yield message.topic.value, json.loads(message.payload)

    async def run_heartbeat(self, interval: float = 2.0) -> None:
        """Publishes to jukebox/<service>/heartbeat until cancelled."""
        topic = f"jukebox/{self.service_name}/heartbeat"
        while True:
            await self.publish(topic, {"service": self.service_name, "ts": time.time()})
            await asyncio.sleep(interval)
