import asyncio
import json
import os

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError

from shared import config, logging as jb_logging
from shared.bus import Bus

SERVICE_NAME = "jb-source-phone"
BLUEZ = "org.bluez"

# Right after an AVRCP renegotiation (BlueZ tearing down the old player
# object and standing up a new one), the browsing channel can still be
# settling and answers with a transient "Device or resource busy" -- retry
# rather than leaving the caller (jb-library's crawl) with a stale result.
DBUS_RETRY_ATTEMPTS = 5
DBUS_RETRY_DELAY_S = 0.5


async def _dbus_retry(coro_fn):
    last_error = None
    for attempt in range(DBUS_RETRY_ATTEMPTS):
        try:
            return await coro_fn()
        except DBusError as e:
            last_error = e
            await asyncio.sleep(DBUS_RETRY_DELAY_S)
    raise last_error

# The AVRCP browse position lives server-side in bluetoothd, not in our
# process -- so a plain in-memory "we started at root" assumption breaks the
# moment our service restarts without the underlying player object also being
# recreated (which only happens on AVRCP renegotiation, not on our restarts).
# Persist it, keyed by player path, so restarts don't lose track of where we are.
NAV_STATE_PATH = "/var/lib/juk3-box/source_phone_nav.json"


def load_nav_state() -> dict:
    if os.path.exists(NAV_STATE_PATH):
        try:
            with open(NAV_STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_nav_scope(player_path: str, scope: str) -> None:
    state = load_nav_state()
    state[player_path] = scope
    with open(NAV_STATE_PATH, "w") as f:
        json.dump(state, f)


class Phone:
    """Wraps the currently connected phone's BlueZ AVRCP objects."""

    def __init__(self, system_bus, device_path: str, player_path: str):
        self.system_bus = system_bus
        self.device_path = device_path
        self.player_path = player_path
        self.player_iface = None
        self.player_props_iface = None
        self.name = None
        # BlueZ's ChangeFolder only moves one level at a time (down into a
        # listed child, or up to the immediate parent) -- there's no jump
        # straight to an arbitrary ancestor. So we track where we are
        # ourselves, persisted across restarts (see NAV_STATE_PATH). A truly
        # new player (never seen before) is assumed to start at Filesystem
        # root per the AVRCP spec default. This only holds if nothing else
        # is driving this player's browse position, true for our appliance.
        self.current_scope = load_nav_state().get(player_path, "root")

    async def setup(self):
        dev_intro = await self.system_bus.introspect(BLUEZ, self.device_path)
        dev_obj = self.system_bus.get_proxy_object(BLUEZ, self.device_path, dev_intro)
        dev_props = dev_obj.get_interface("org.freedesktop.DBus.Properties")
        name_variant = await dev_props.call_get("org.bluez.Device1", "Name")
        self.name = name_variant.value

        player_intro = await self.system_bus.introspect(BLUEZ, self.player_path)
        player_obj = self.system_bus.get_proxy_object(BLUEZ, self.player_path, player_intro)
        self.player_iface = player_obj.get_interface("org.bluez.MediaPlayer1")
        self.folder_iface = player_obj.get_interface("org.bluez.MediaFolder1")
        self.player_props_iface = player_obj.get_interface("org.freedesktop.DBus.Properties")

    async def now_playing(self) -> dict:
        props = await self.player_props_iface.call_get_all("org.bluez.MediaPlayer1")
        track = props["Track"].value if "Track" in props else {}
        return {
            "status": props.get("Status").value if "Status" in props else None,
            "position_ms": props.get("Position").value if "Position" in props else None,
            "title": track.get("Title").value if "Title" in track else None,
            "artist": track.get("Artist").value if "Artist" in track else None,
            "album": track.get("Album").value if "Album" in track else None,
            "genre": track.get("Genre").value if "Genre" in track else None,
            "duration_ms": track.get("Duration").value if "Duration" in track else None,
            "track_number": track.get("TrackNumber").value if "TrackNumber" in track else None,
            "number_of_tracks": track.get("NumberOfTracks").value if "NumberOfTracks" in track else None,
        }

    async def _enter_folder_by_name(self, name_suffix: str):
        items = await self.folder_iface.call_list_items({})
        for path, props in items.items():
            name = props["Name"].value if "Name" in props else ""
            if name.endswith(name_suffix):
                await self.folder_iface.call_change_folder(path)
                return
        raise RuntimeError(f"folder not found: {name_suffix}")

    async def crawl_songs(self) -> list:
        """Navigate Filesystem > My Music > Songs and list every track (title only, per AVRCP limits)."""
        if self.current_scope != "songs":
            if self.current_scope != "root":
                raise RuntimeError(
                    f"can't navigate to Songs from unknown scope {self.current_scope!r} "
                    "(ChangeFolder can't jump straight back to root)"
                )
            await _dbus_retry(lambda: self._enter_folder_by_name("/My Music"))
            await _dbus_retry(lambda: self._enter_folder_by_name("/Songs"))
            self.current_scope = "songs"
            save_nav_scope(self.player_path, self.current_scope)
        items = await _dbus_retry(lambda: self.folder_iface.call_list_items({}))
        return [
            {"item_path": path, "title": props["Name"].value if "Name" in props else None}
            for path, props in items.items()
        ]

    async def play_item(self, item_path: str):
        item_intro = await self.system_bus.introspect(BLUEZ, item_path)
        item_obj = self.system_bus.get_proxy_object(BLUEZ, item_path, item_intro)
        item_iface = item_obj.get_interface("org.bluez.MediaItem1")
        await item_iface.call_play()

    async def transport(self, action: str):
        method = {
            "play": self.player_iface.call_play,
            "pause": self.player_iface.call_pause,
            "next": self.player_iface.call_next,
            "previous": self.player_iface.call_previous,
        }[action]
        await method()


async def find_connected_player(system_bus) -> tuple[str, str] | None:
    intro = await system_bus.introspect(BLUEZ, "/")
    obj = system_bus.get_proxy_object(BLUEZ, "/", intro)
    om = obj.get_interface("org.freedesktop.DBus.ObjectManager")
    objects = await om.call_get_managed_objects()
    for path, interfaces in objects.items():
        if "org.bluez.MediaPlayer1" in interfaces:
            device_path = str(path).rsplit("/player", 1)[0]
            return device_path, str(path)
    return None


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    system_bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    phone: Phone | None = None

    async def connect_current_phone(bus: Bus, retries: int = 5, retry_delay: float = 0.8):
        nonlocal phone
        found = await find_connected_player(system_bus)
        if not found:
            phone = None
            await bus.publish(f"jukebox/{SERVICE_NAME}/connected", {"connected": False}, retain=True)
            return
        device_path, player_path = found
        # Right after (re)connect, BlueZ sometimes registers MediaPlayer1
        # slightly before MediaFolder1/Properties are fully up on the same
        # object -- setup() can throw on that race. Retry a few times
        # rather than leaving a half-initialized Phone sitting around
        # (which would spam AttributeErrors from poll_now_playing forever
        # and never republish "connected").
        for attempt in range(1, retries + 1):
            candidate = Phone(system_bus, device_path, player_path)
            try:
                await candidate.setup()
            except Exception:
                log.warning(
                    "phone setup failed (attempt %d/%d), retrying", attempt, retries, exc_info=True
                )
                await asyncio.sleep(retry_delay)
                continue
            phone = candidate
            phone.mac = device_path.rsplit("dev_", 1)[-1].replace("_", ":")
            log.info("phone connected: %s (%s)", phone.name, phone.mac)
            await bus.publish(
                f"jukebox/{SERVICE_NAME}/connected",
                {"connected": True, "name": phone.name, "mac": phone.mac},
                retain=True,
            )
            return
        log.error("phone setup failed after %d attempts, giving up", retries)
        phone = None
        await bus.publish(f"jukebox/{SERVICE_NAME}/connected", {"connected": False}, retain=True)

    async def handle_command(bus: Bus, cmd: dict):
        nonlocal phone
        action = cmd.get("action")
        if action == "refresh_connection":
            await connect_current_phone(bus)
            return
        if phone is None:
            log.warning("command %r received with no phone connected", action)
            return
        if action in ("play", "pause", "next", "previous"):
            await phone.transport(action)
        elif action == "play_item":
            try:
                await phone.play_item(cmd["item_path"])
            except Exception:
                log.exception("play_item failed: %r", cmd["item_path"])
                await bus.publish(f"jukebox/{SERVICE_NAME}/play-error", cmd)
        elif action == "crawl":
            tracks = await phone.crawl_songs()
            await bus.publish(
                f"jukebox/{SERVICE_NAME}/crawl-result",
                {"mac": phone.mac, "tracks": tracks},
            )
            log.info("crawl complete: %d tracks", len(tracks))
        else:
            log.warning("unknown command action: %r", action)

    async def poll_now_playing(bus: Bus):
        last_track_title = None
        while True:
            if phone is not None:
                try:
                    np = await phone.now_playing()
                    if np["title"] != last_track_title:
                        last_track_title = np["title"]
                    await bus.publish(f"jukebox/{SERVICE_NAME}/now-playing", np)
                except Exception:
                    log.exception("failed to read now-playing state")
            await asyncio.sleep(2)

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])

        # Subscribe before publishing "connected" -- otherwise another
        # service reacting to that retained message (e.g. jb-library
        # requesting a crawl) can race ahead of our own subscription and
        # have its command silently dropped (QoS0, no retain on /cmd).
        messages = await bus.subscribe(f"jukebox/{SERVICE_NAME}/cmd")

        await connect_current_phone(bus)

        # BlueZ recreates the MediaPlayer1 object (e.g. player0 -> player1) on
        # AVRCP renegotiation even while nominally still connected, so we
        # can't cache a player path once and assume it stays valid.
        intro = await system_bus.introspect(BLUEZ, "/")
        root_obj = system_bus.get_proxy_object(BLUEZ, "/", intro)
        om = root_obj.get_interface("org.freedesktop.DBus.ObjectManager")

        def on_interfaces_added(path, interfaces):
            if "org.bluez.MediaPlayer1" in interfaces:
                log.info("MediaPlayer1 appeared at %s, reconnecting", path)
                asyncio.create_task(connect_current_phone(bus))

        def on_interfaces_removed(path, interfaces):
            if phone is not None and "org.bluez.MediaPlayer1" in interfaces and str(path) == phone.player_path:
                log.info("MediaPlayer1 disappeared at %s", path)
                asyncio.create_task(connect_current_phone(bus))

        om.on_interfaces_added(on_interfaces_added)
        om.on_interfaces_removed(on_interfaces_removed)

        asyncio.create_task(bus.run_heartbeat())
        asyncio.create_task(poll_now_playing(bus))
        async for _topic, payload in messages:
            try:
                await handle_command(bus, payload)
            except Exception:
                log.exception("failed to handle command: %r", payload)


if __name__ == "__main__":
    asyncio.run(main())
