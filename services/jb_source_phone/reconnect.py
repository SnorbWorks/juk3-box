"""Reconnecting the phone on its own (dev plan Phase 4: auto-reconnect).

The user's policy (option C, 2026-10-12):
  - at boot (service start) with no phone connected: try for 10 minutes
  - a connected phone drops while music was playing: try for 3 minutes
    (usually range or a glitch -- people rarely switch speakers mid-song)
  - a phone that drops while idle is left alone: it may have been the
    owner's choice, and calling it would pull the phone's audio away from
    whatever it switched to (iOS routes audio to a speaker that connects)
BlueZ 5.82 here doesn't say why a link ended, hence the playing/idle rule.

Phones are tried most recently used first; the phone book keeps one entry
per phone (name, last connected), overwritten each time.
"""

import asyncio
import json
from pathlib import Path

from shared.files import write_atomic

BLUEZ = "org.bluez"
BOOK_PATH = Path("/var/lib/juk3-box/phones.json")
A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"     # we're advertising as a speaker
A2DP_SOURCE = "0000110a-0000-1000-8000-00805f9b34fb"   # the device can send us audio

BOOT_WINDOW_S = 600
DROP_WINDOW_S = 180
CONNECT_TIMEOUT_S = 20


def window_for(event: str, was_playing: bool = False):
    """How long to keep trying after `event` ("boot" or "drop"), or None."""
    if event == "boot":
        return BOOT_WINDOW_S
    if event == "drop" and was_playing:
        return DROP_WINDOW_S
    return None


def retry_delay(elapsed_s: float) -> float:
    return 15.0 if elapsed_s < 60 else 30.0


class PhoneBook:
    def __init__(self, path: Path = BOOK_PATH):
        self.path = path
        try:
            self.phones = json.loads(path.read_text())
        except (OSError, ValueError):
            self.phones = {}

    def touch(self, mac: str, name: str, now: float):
        self.phones[mac] = {"name": name, "last_connected": now}
        write_atomic(self.path, json.dumps(self.phones, indent=1))

    def forget(self, mac: str):
        if self.phones.pop(mac, None) is not None:
            write_atomic(self.path, json.dumps(self.phones, indent=1))

    def order(self, macs: list) -> list:
        """Most recently connected first; phones never seen last."""
        return sorted(macs, key=lambda m: -(self.phones.get(m, {}).get("last_connected") or 0))


ADAPTER = "/org/bluez/hci0"


async def _props(system_bus, path: str, iface: str) -> dict:
    intro = await system_bus.introspect(BLUEZ, path)
    p = system_bus.get_proxy_object(BLUEZ, path, intro).get_interface("org.freedesktop.DBus.Properties")
    return await p.call_get_all(iface)


async def speaker_ready(system_bus) -> bool:
    """True once WirePlumber has registered the A2DP sink (~2 min into boot);
    a phone can't connect for audio before that."""
    adapter = await _props(system_bus, ADAPTER, "org.bluez.Adapter1")
    return A2DP_SINK in [u.lower() for u in adapter["UUIDs"].value]


async def phones(system_bus) -> list:
    """Paired, trusted devices that can send audio: [(mac, path, name, connected)].

    Reads just the adapter's device nodes and each one's Device1 properties --
    not GetManagedObjects, which returns every object BlueZ holds, including
    each song and album the phone exposes for browsing (1,841 of them with
    the user's library), and polled every 3 s that cost a third of a CPU core
    (2026-10-12)."""
    out = []
    for node in (await system_bus.introspect(BLUEZ, ADAPTER)).nodes:
        if not node.name.startswith("dev_"):
            continue
        path = f"{ADAPTER}/{node.name}"
        try:
            dev = await _props(system_bus, path, "org.bluez.Device1")
        except Exception:
            continue  # gone between listing and reading
        uuids = [u.lower() for u in dev["UUIDs"].value] if "UUIDs" in dev else []
        if dev["Paired"].value and dev["Trusted"].value and A2DP_SOURCE in uuids:
            name = dev["Name"].value if "Name" in dev else dev["Address"].value
            out.append((dev["Address"].value, path, name, dev["Connected"].value))
    return out


async def connect(system_bus, path: str):
    """Connect the phone's audio (A2DP source) profile.

    Not Device1.Connect(): an iPhone is dual-mode and keeps sending Bluetooth
    LE beacons from rotating random addresses, so BlueZ often picks LE for a
    plain Connect and then waits for the phone on LE forever (btmon showed
    only LE scanning, ending in le-connection-abort-by-local; 2026-10-12).
    Asking for the A2DP profile forces classic Bluetooth, which is what audio
    uses -- that connected in 2 s where Connect() hung. A failed attempt is
    cleared with Disconnect() so it can't leave BlueZ stuck "in progress"
    and swallow every later attempt."""
    intro = await system_bus.introspect(BLUEZ, path)
    dev = system_bus.get_proxy_object(BLUEZ, path, intro).get_interface("org.bluez.Device1")
    try:
        await asyncio.wait_for(dev.call_connect_profile(A2DP_SOURCE), CONNECT_TIMEOUT_S)
    except Exception:
        try:
            await asyncio.wait_for(dev.call_disconnect(), 5)
        except Exception:
            pass
        raise
