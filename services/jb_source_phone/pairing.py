"""Pairing a new phone (dev plan Phase 4: pairing mode).

Pairing is opened from the jukebox (its menu or the admin) for two minutes:
the jukebox becomes visible and pairable under its own name. A phone that
pairs shows a 6-digit code, and so does the jukebox; the pairing goes
through only once someone confirms it on the jukebox (the knob, or the
admin) -- the user's choice, 2026-10-14, so a stranger nearby can't pair
during the window. Outside the window the jukebox is invisible and refuses
to pair.

The Agent is BlueZ's way of asking us: registered as the default agent with
DisplayYesNo, which gives numeric comparison (the code on both screens)
with phones. The window and the questions are reported on
jukebox/jb-source-phone/pairing (retained) as
  {"state": "idle" | "waiting" | "confirm" | "paired" | "failed",
   "until": epoch s and "jukebox": the name to tap (waiting),
   "code": "123456", "name": phone name, "mac": ...}
"""

import asyncio
import time

from dbus_next import Variant
from dbus_next.errors import DBusError
from dbus_next.service import ServiceInterface, method

BLUEZ = "org.bluez"
AGENT_PATH = "/juk3box/agent"
ADAPTER = "/org/bluez/hci0"
WINDOW_S = 120
CONFIRM_WAIT_S = 60          # how long a code waits for someone at the jukebox
RESULT_SHOWN_S = 8           # "paired" / "failed" stay up this long, then idle


def rejected(why: str) -> DBusError:
    return DBusError("org.bluez.Error.Rejected", why)


class Agent(ServiceInterface):
    def __init__(self, pairing: "Pairing"):
        super().__init__("org.bluez.Agent1")
        self.pairing = pairing

    @method()
    def Release(self):
        pass

    @method()
    async def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821
        await self.pairing.confirm(device, f"{passkey:06d}")

    @method()
    async def RequestAuthorization(self, device: "o"):  # noqa: F821
        # Pairing with no code to compare (a phone that can't show one):
        # still only in the window, and still confirmed at the jukebox.
        await self.pairing.confirm(device, None)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821
        # Asked only for devices that aren't trusted yet: the phone being
        # paired, while the window is open.
        if not self.pairing.is_open():
            raise rejected("not pairing")

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: F821
        raise rejected("PIN pairing isn't supported")

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: F821
        raise rejected("passkey entry isn't supported")

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821
        pass

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821
        pass

    @method()
    def Cancel(self):
        self.pairing.cancelled()


class Pairing:
    """The pairing window and the question in front of the jukebox."""

    def __init__(self, system_bus, publish, log):
        self.system_bus = system_bus
        self.publish = publish           # async fn(state dict)
        self.log = log
        self.open_until = 0.0
        self.answer: asyncio.Future | None = None
        self.asking: dict | None = None  # {"code", "name", "mac", "device"}
        self.closer: asyncio.Task | None = None
        self.on_paired = None            # async fn(mac, name), set by main
        self.claim = None                # fn(mac): the jukebox is about to be this phone's
        self.alias = None                # the name phones see the jukebox by

    async def register(self):
        intro = await self.system_bus.introspect(BLUEZ, "/org/bluez")
        manager = self.system_bus.get_proxy_object(BLUEZ, "/org/bluez", intro).get_interface("org.bluez.AgentManager1")
        self.system_bus.export(AGENT_PATH, Agent(self))
        await manager.call_register_agent(AGENT_PATH, "DisplayYesNo")
        await manager.call_request_default_agent(AGENT_PATH)
        await self._adapter(pairable=False, discoverable=False)
        try:
            intro = await self.system_bus.introspect(BLUEZ, ADAPTER)
            props = self.system_bus.get_proxy_object(BLUEZ, ADAPTER, intro).get_interface("org.freedesktop.DBus.Properties")
            self.alias = (await props.call_get("org.bluez.Adapter1", "Alias")).value
        except Exception:
            self.log.warning("couldn't read the jukebox's Bluetooth name")
        await self.publish({"state": "idle"})

    async def _adapter(self, pairable: bool, discoverable: bool):
        intro = await self.system_bus.introspect(BLUEZ, ADAPTER)
        props = self.system_bus.get_proxy_object(BLUEZ, ADAPTER, intro).get_interface("org.freedesktop.DBus.Properties")
        if discoverable:
            await props.call_set("org.bluez.Adapter1", "DiscoverableTimeout", Variant("u", WINDOW_S))
            await props.call_set("org.bluez.Adapter1", "PairableTimeout", Variant("u", WINDOW_S))
        await props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", pairable))
        await props.call_set("org.bluez.Adapter1", "Discoverable", Variant("b", discoverable))

    def is_open(self) -> bool:
        return time.time() < self.open_until

    async def start(self):
        self.open_until = time.time() + WINDOW_S
        await self._adapter(pairable=True, discoverable=True)
        self.log.info("pairing open for %ds", WINDOW_S)
        await self.publish({"state": "waiting", "until": self.open_until, "jukebox": self.alias})
        if self.closer:
            self.closer.cancel()
        self.closer = asyncio.create_task(self._close_later(WINDOW_S))

    async def _close_later(self, delay: float):
        await asyncio.sleep(delay)
        if self.asking:
            return   # a code is on screen: it closes when that's answered
        await self.stop("timed out")

    async def stop(self, why: str, state: str = "idle", **extra):
        self.open_until = 0.0
        if self.closer and self.closer is not asyncio.current_task():
            self.closer.cancel()
        self.closer = None
        if self.answer and not self.answer.done():
            self.answer.set_result(False)
        try:
            await self._adapter(pairable=False, discoverable=False)
        except Exception:
            self.log.exception("couldn't close pairing on the adapter")
        self.log.info("pairing closed (%s)", why)
        await self.publish({"state": state, **extra})
        if state != "idle":
            await asyncio.sleep(RESULT_SHOWN_S)
            if not self.is_open() and not self.asking:
                await self.publish({"state": "idle"})

    async def confirm(self, device: str, code: str | None):
        """A phone wants to pair: ask at the jukebox, and answer BlueZ."""
        if not self.is_open():
            raise rejected("not pairing")
        if self.asking:
            raise rejected("already asking about another phone")
        mac = device.rsplit("dev_", 1)[-1].replace("_", ":")
        name = await self._name(device) or mac
        self.asking = {"code": code, "name": name, "mac": mac, "device": device}
        self.answer = asyncio.get_running_loop().create_future()
        self.log.info("pairing request from %s (%s)", name, mac)
        await self.publish({"state": "confirm", "code": code, "name": name, "mac": mac})
        try:
            ok = await asyncio.wait_for(self.answer, CONFIRM_WAIT_S)
        except asyncio.TimeoutError:
            ok = False
        asking, self.asking = self.asking, None
        if not ok:
            asyncio.create_task(self.stop("not confirmed", "failed", name=asking["name"]))
            raise rejected("not confirmed at the jukebox")
        # Accepted: BlueZ finishes the pairing; watch for it to complete.
        asyncio.create_task(self._finish(device, asking))

    async def _finish(self, device: str, asking: dict):
        for _ in range(30):
            await asyncio.sleep(1)
            try:
                props = await self._props(device)
            except Exception:
                continue
            if props.get("Paired") and props["Paired"].value:
                # Hold the jukebox for it before trusting it, so it isn't
                # turned away for connecting while another phone has it.
                if self.claim:
                    self.claim(asking["mac"])
                intro = await self.system_bus.introspect(BLUEZ, device)
                p = self.system_bus.get_proxy_object(BLUEZ, device, intro).get_interface("org.freedesktop.DBus.Properties")
                await p.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
                self.log.info("paired with %s (%s)", asking["name"], asking["mac"])
                asyncio.create_task(self.stop("paired", "paired", name=asking["name"], mac=asking["mac"]))
                if self.on_paired:
                    await self.on_paired(asking["mac"], asking["name"])
                return
        await self.stop("pairing didn't complete", "failed", name=asking["name"])

    def answered(self, ok: bool):
        if self.answer and not self.answer.done():
            self.answer.set_result(ok)

    def cancelled(self):
        """The phone (or BlueZ) gave up on the pairing."""
        self.answered(False)

    async def _props(self, device: str) -> dict:
        intro = await self.system_bus.introspect(BLUEZ, device)
        p = self.system_bus.get_proxy_object(BLUEZ, device, intro).get_interface("org.freedesktop.DBus.Properties")
        return await p.call_get_all("org.bluez.Device1")

    async def _name(self, device: str) -> str | None:
        try:
            props = await self._props(device)
        except Exception:
            return None
        for key in ("Alias", "Name"):
            if key in props:
                return props[key].value
        return None
