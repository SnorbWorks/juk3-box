"""Waking the helper app over Bluetooth LE (docs/contracts/helper-protocol.md).

iOS suspends the app when it's idle and its WebSocket goes with it. The app
stays connected to this small BLE service instead (background mode
bluetooth-central), and iOS wakes it when the "wake" characteristic
notifies: it reconnects and takes the waiting command.

A GATT application and an advertisement registered with BlueZ over D-Bus,
beside the Bluetooth Classic audio (jb-source-phone) on the same adapter.
Only while at least one helper phone is paired: nothing changes for a
jukebox nobody uses the app with.
"""

import struct

from dbus_next import BusType, Variant
from dbus_next.aio import MessageBus
from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method

SERVICE_UUID = "4a554b45-0001-4c80-8000-6a756b33626f"
WAKE_UUID = "4a554b45-0002-4c80-8000-6a756b33626f"
ID_UUID = "4a554b45-0003-4c80-8000-6a756b33626f"

ROOT = "/com/juk3box/helper"
SERVICE_PATH = f"{ROOT}/service0"
WAKE_PATH = f"{SERVICE_PATH}/char0"
ID_PATH = f"{SERVICE_PATH}/char1"
ADV_PATH = f"{ROOT}/adv0"
ADAPTER = "/org/bluez/hci0"


class GattService(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.GattService1")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return SERVICE_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return True


class Characteristic(ServiceInterface):
    def __init__(self, uuid: str, flags: list, value: bytes):
        super().__init__("org.bluez.GattCharacteristic1")
        self._uuid, self._flags, self._value = uuid, flags, value
        self.notifying = False

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return SERVICE_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return self._flags

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":
        return self._value

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        return self._value

    @method()
    def StartNotify(self):
        self.notifying = True

    @method()
    def StopNotify(self):
        self.notifying = False

    def set(self, value: bytes) -> None:
        self._value = value
        if self.notifying:
            self.emit_properties_changed({"Value": value})


class Advertisement(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.LEAdvertisement1")

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return [SERVICE_UUID]

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return "juk3box"

    @method()
    def Release(self):
        pass


class ObjectManager(ServiceInterface):
    """What BlueZ reads to learn the GATT application's objects."""

    def __init__(self, objects: dict):
        super().__init__("org.freedesktop.DBus.ObjectManager")
        self._objects = objects       # path -> ServiceInterface

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        out = {}
        for path, iface in self._objects.items():
            props = {}
            for name, sig in PROPS[iface.name]:
                props[name] = Variant(sig, getattr(iface, name))   # dbus_property is a property
            out[path] = {iface.name: props}
        return out


PROPS = {
    "org.bluez.GattService1": [("UUID", "s"), ("Primary", "b")],
    "org.bluez.GattCharacteristic1": [("UUID", "s"), ("Service", "o"), ("Flags", "as"), ("Value", "ay")],
}


class Wake:
    """The BLE side. running: registered with BlueZ and advertising."""

    def __init__(self, jukebox_id: str, log):
        self.jukebox_id, self.log = jukebox_id, log
        self.running = False
        self.count = 0
        self._bus = None
        self._wake = Characteristic(WAKE_UUID, ["read", "notify"], struct.pack("<I", 0))
        self._id = Characteristic(ID_UUID, ["read"], jukebox_id.encode())

    @staticmethod
    def uuids() -> dict:
        return {"service": SERVICE_UUID, "wake": WAKE_UUID, "id": ID_UUID}

    async def ensure_running(self, wanted: bool) -> None:
        if wanted and not self.running:
            await self.start()
        elif not wanted and self.running:
            await self.stop()

    async def start(self) -> None:
        try:
            self._bus = bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            service = GattService()
            bus.export(SERVICE_PATH, service)
            bus.export(WAKE_PATH, self._wake)
            bus.export(ID_PATH, self._id)
            bus.export(ROOT, ObjectManager({SERVICE_PATH: service, WAKE_PATH: self._wake, ID_PATH: self._id}))
            bus.export(ADV_PATH, Advertisement())
            adapter = bus.get_proxy_object("org.bluez", ADAPTER, await bus.introspect("org.bluez", ADAPTER))
            await adapter.get_interface("org.bluez.GattManager1").call_register_application(ROOT, {})
            await adapter.get_interface("org.bluez.LEAdvertisingManager1").call_register_advertisement(ADV_PATH, {})
            self.running = True
            self.log.info("Bluetooth LE wake-up advertising")
        except Exception as e:
            self.log.warning("Bluetooth LE wake-up unavailable: %s", e)
            await self.stop()

    async def stop(self) -> None:
        bus, self._bus, self.running = self._bus, None, False
        if bus:
            try:
                adapter = bus.get_proxy_object("org.bluez", ADAPTER, await bus.introspect("org.bluez", ADAPTER))
                await adapter.get_interface("org.bluez.LEAdvertisingManager1").call_unregister_advertisement(ADV_PATH)
                await adapter.get_interface("org.bluez.GattManager1").call_unregister_application(ROOT)
            except Exception:
                pass
            bus.disconnect()

    async def wake(self) -> None:
        """Notifies the wake characteristic (a new count each time)."""
        self.count = (self.count + 1) & 0xFFFFFFFF
        self._wake.set(struct.pack("<I", self.count))
