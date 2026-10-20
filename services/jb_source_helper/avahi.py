"""Announces the jukebox to the helper app over Bonjour (_juk3box._tcp),
through avahi-daemon's D-Bus API: no root, no file in /etc/avahi."""

from dbus_next import BusType
from dbus_next.aio import MessageBus

SERVICE_TYPE = "_juk3box._tcp"
IF_UNSPEC, PROTO_UNSPEC = -1, -1


def txt(jukebox_id: str, name: str, path: str = "/helper") -> list[bytes]:
    return [f"id={jukebox_id}".encode(), f"name={name}".encode(), b"v=1", f"path={path}".encode()]


class Announcement:
    def __init__(self, log):
        self.log = log
        self._bus = None
        self._group = None

    async def start(self, name: str, port: int, records: list[bytes]) -> None:
        try:
            self._bus = bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            server = bus.get_proxy_object("org.freedesktop.Avahi", "/",
                                          await bus.introspect("org.freedesktop.Avahi", "/"))
            path = await server.get_interface("org.freedesktop.Avahi.Server").call_entry_group_new()
            group = bus.get_proxy_object("org.freedesktop.Avahi", path,
                                         await bus.introspect("org.freedesktop.Avahi", path))
            self._group = group.get_interface("org.freedesktop.Avahi.EntryGroup")
            await self._group.call_add_service(IF_UNSPEC, PROTO_UNSPEC, 0, name, SERVICE_TYPE, "", "", port, records)
            await self._group.call_commit()
            self.log.info("announced %s on port %d", SERVICE_TYPE, port)
        except Exception as e:
            self.log.warning("couldn't announce over Bonjour: %s", e)

    async def stop(self) -> None:
        try:
            if self._group:
                await self._group.call_free()
        except Exception:
            pass
        if self._bus:
            self._bus.disconnect()
