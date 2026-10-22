"""MQTT bus client shared by every jb-* service."""

import asyncio
import itertools
import json
import logging
import os
import time
from pathlib import Path

import aiomqtt

# Waits between tries to reach the broker, the last one repeating: a
# service rides out mosquitto restarting (or dropping everyone when the
# clock jumps at boot on a board without a clock battery) instead of exiting.
RETRY_S = (0.5, 1, 2, 5)


def module_login() -> dict | None:
    """An installed module's private bus login (docs/contracts/installed-modules.md,
    Bus access): {"socket", "username", "password"}, handed over by systemd
    as the "bus" credential. None for built-in modules."""
    folder = os.environ.get("CREDENTIALS_DIRECTORY")
    if not folder:
        return None
    try:
        login = json.loads((Path(folder) / "bus").read_text())
    except (OSError, ValueError):
        return None
    return login if isinstance(login, dict) and login.get("socket") else None


class Bus:
    """Wraps an aiomqtt client with JSON helpers and a heartbeat publisher.
    It reconnects by itself when the broker goes away, subscribing again to
    every topic; publishing waits until it's back."""

    def __init__(self, service_name: str, host: str = "localhost", port: int = 1883):
        self.service_name = service_name
        self.host = host
        self.port = port
        self._client: aiomqtt.Client | None = None
        self._topics: list[str] = []
        self._lock = asyncio.Lock()
        self._log = logging.getLogger(f"{service_name}.bus")

    def _new_client(self) -> aiomqtt.Client:
        login = module_login()
        if login:
            # An installed module: its own login, over the private socket.
            return aiomqtt.Client(login["socket"], transport="unix",
                                  username=login.get("username"), password=login.get("password"))
        return aiomqtt.Client(self.host, port=self.port)

    async def _connect(self) -> None:
        for attempt in itertools.count():
            client = self._new_client()
            try:
                await client.__aenter__()
            except aiomqtt.MqttError as e:
                if attempt == 0:
                    self._log.warning("can't reach the bus yet (%s); trying again", e)
                await asyncio.sleep(RETRY_S[min(attempt, len(RETRY_S) - 1)])
                continue
            try:
                for topic in self._topics:
                    await client.subscribe(topic)
            except aiomqtt.MqttError:
                await self._close(client)
                await asyncio.sleep(RETRY_S[min(attempt, len(RETRY_S) - 1)])
                continue
            self._client = client
            return

    async def _reconnect(self, failed: aiomqtt.Client) -> None:
        """After `failed` lost the broker: one new connection, however many
        tasks noticed."""
        async with self._lock:
            if self._client is not failed:
                return
            self._log.warning("lost the bus; reconnecting")
            await self._close(failed)
            await self._connect()
            self._log.info("back on the bus")

    @staticmethod
    async def _close(client: aiomqtt.Client) -> None:
        try:
            await client.__aexit__(None, None, None)
        except Exception:  # already gone
            pass

    async def __aenter__(self):
        await self._connect()
        return self

    async def __aexit__(self, *exc_info):
        await self._close(self._client)

    async def _publish(self, topic: str, payload, retain: bool) -> None:
        while True:
            client = self._client
            try:
                await client.publish(topic, payload, retain=retain)
                return
            except aiomqtt.MqttError:
                await self._reconnect(client)

    async def publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        await self._publish(topic, json.dumps(payload), retain)

    async def clear_retained(self, topic: str) -> None:
        """Deletes the broker's retained message on this topic (MQTT: an
        empty retained payload). Subscribers never see the deletion itself --
        _iter_messages skips empty payloads."""
        await self._publish(topic, b"", True)

    async def subscribe(self, topic: str):
        """Subscribes immediately and returns an async iterator of messages.

        Split into two steps (subscribe now, iterate later) on purpose: a
        plain async-generator function doesn't run any of its body -- so
        doesn't actually register the MQTT subscription -- until the first
        time it's iterated. Anything published to this topic between
        startup and "async for ... in bus.subscribe(...)" would otherwise be
        silently dropped (QoS0, no retain), which matters a lot right after
        a coordinated restart of multiple services that trigger each other.
        """
        if topic not in self._topics:
            self._topics.append(topic)      # for subscribing again after a reconnect
        client = self._client
        try:
            await client.subscribe(topic)
        except aiomqtt.MqttError:
            await self._reconnect(client)   # which subscribes to it
        return self._iter_messages()

    async def _iter_messages(self):
        while True:
            client = self._client
            try:
                async for message in client.messages:
                    if not message.payload:
                        continue  # a retained-message deletion, not a real message
                    yield message.topic.value, json.loads(message.payload)
            except aiomqtt.MqttError:
                await self._reconnect(client)
                continue
            if self._client is client:
                return  # closed

    async def run_heartbeat(self, interval: float = 2.0) -> None:
        """Publishes to jukebox/<service>/heartbeat until cancelled."""
        topic = f"jukebox/{self.service_name}/heartbeat"
        while True:
            await self.publish(topic, {"service": self.service_name, "ts": time.time()})
            await asyncio.sleep(interval)
