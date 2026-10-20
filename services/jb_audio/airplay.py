"""The AirPlay receivers on the network, for choosing a whole-home zone.

Browses _raop._tcp through the Pi's avahi-daemon over D-Bus. Service names
are "<device id>@<name>"; the name is what a phone's AirPlay menu shows.
"""

import asyncio
import logging

from dbus_next import BusType, Message
from dbus_next.aio import MessageBus

AVAHI = "org.freedesktop.Avahi"
BROWSER_IFACE = "org.freedesktop.Avahi.ServiceBrowser"
IF_UNSPEC, PROTO_INET = -1, 0
# Avahi's lookup-result flags: published on this machine. The jukebox's own
# AirPlay receiver (jb-airplay, for the helper app) isn't a whole-home zone:
# sending the cabinet to itself would loop.
LOCAL, OUR_OWN = 8, 16

log = logging.getLogger("jb-audio.airplay")


def display_name(service_name: str) -> str:
    return service_name.split("@", 1)[1] if "@" in service_name else service_name


class Receivers:
    """Keeps .names (sorted, unique) up to date; calls on_change(names)."""

    def __init__(self, on_change=None):
        self.on_change = on_change
        self._services: dict[str, int] = {}   # display name -> how many (IPv4 entries per interface)
        self.names: list[str] = []

    def _changed(self):
        names = sorted(self._services, key=str.casefold)
        if names != self.names:
            self.names = names
            if self.on_change:
                asyncio.create_task(self.on_change(names))

    def _signal(self, msg: Message):
        if msg.interface != BROWSER_IFACE or msg.member not in ("ItemNew", "ItemRemove"):
            return
        name = display_name(msg.body[2])
        if msg.body[5] & (LOCAL | OUR_OWN):
            return
        if msg.member == "ItemNew":
            self._services[name] = self._services.get(name, 0) + 1
        elif name in self._services:
            self._services[name] -= 1
            if self._services[name] <= 0:
                del self._services[name]
        self._changed()

    async def run(self):
        """Browse until cancelled; if avahi isn't reachable, retry later."""
        while True:
            try:
                bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
                bus.add_message_handler(self._signal)
                await bus.call(Message(
                    destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                    body=[f"type='signal',interface='{BROWSER_IFACE}'"]))
                # Prepare-then-Start (avahi >= 0.8), so no ItemNew lands before
                # we're listening.
                reply = await bus.call(Message(
                    destination=AVAHI, path="/", interface="org.freedesktop.Avahi.Server2",
                    member="ServiceBrowserPrepare", signature="iissu",
                    body=[IF_UNSPEC, PROTO_INET, "_raop._tcp", "local", 0]))
                path = reply.body[0]
                await bus.call(Message(destination=AVAHI, path=path, interface=BROWSER_IFACE, member="Start"))
                await bus.wait_for_disconnect()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("couldn't browse for AirPlay receivers; trying again in 60s", exc_info=True)
            self._services.clear()
            self._changed()
            await asyncio.sleep(60)
