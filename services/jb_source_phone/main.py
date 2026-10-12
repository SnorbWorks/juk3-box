import asyncio

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError, InterfaceNotFoundError

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
BUSY_WAIT_S = 60

# A whole library crawl normally takes well under a minute. BlueZ calls have
# no timeout of their own, and a crawl that hung when the phone switched
# apps mid-way held the sync lock forever, so no later sync ever ran (seen
# 2026-10-09). Give up and let the next player change try again.
CRAWL_TIMEOUT_S = 120

# Right after a connect the phone's browse root reports 0 items for a moment
# and answers "busy"; a sync started then fails (seen 2026-10-11). Wait for
# the folder to report items first.
BROWSE_READY_TIMEOUT_S = 15
# A failed sync retries after these delays (the first try is immediate).
SYNC_RETRY_DELAYS_S = (3, 10, 30)
# Lost browsing (the phone's cursor somewhere Songs can't be reached from)
# is reset by reconnecting the phone -- the only way back to the browse root.
# It drops the audio link, so never while playing, and rate-limited so a
# phone that keeps getting lost can't cause a reconnect loop.
RESET_MIN_INTERVAL_S = 300
RESET_WAIT_POLL_S = 60
# Browse lease (jb-crawl's deep crawl takes turns with the library sync).
LEASE_MAX_S = 1200          # a lease can't outlive this, whatever was asked
LEASE_REVOKE_WAIT_S = 30    # after a pick revokes it, how long the holder gets to hand back
LEASE_CHECK_S = 2


class BrowseLost(RuntimeError):
    """The phone's browse cursor is somewhere the Songs list can't be reached
    from (Playlists, NowPlaying, deep in another branch)."""


async def _dbus_retry(coro_fn):
    """Retries a BlueZ browsing call. "Busy" is waited out for up to
    BUSY_WAIT_S: after any listing BlueZ keeps fetching the rest of that
    folder from the phone in the background and refuses calls until it's
    done (seen in btmon 2026-10-11; a big folder takes a while), and a sync
    that gave up on it left the library unsynced. Other errors get the
    usual few quick retries."""
    last_error = None
    attempts = 0
    busy_until = asyncio.get_running_loop().time() + BUSY_WAIT_S
    while True:
        try:
            return await coro_fn()
        except DBusError as e:
            last_error = e
            if "busy" in str(e).lower() and asyncio.get_running_loop().time() < busy_until:
                await asyncio.sleep(DBUS_RETRY_DELAY_S)
                continue
            attempts += 1
            if attempts >= DBUS_RETRY_ATTEMPTS:
                raise last_error
            await asyncio.sleep(DBUS_RETRY_DELAY_S)


class Phone:
    """Wraps the currently connected phone's BlueZ AVRCP objects."""

    def __init__(self, system_bus, device_path: str, player_path: str):
        self.system_bus = system_bus
        self.device_path = device_path
        self.player_path = player_path
        self.player_iface = None
        self.player_props_iface = None
        self.name = None

    async def setup(self):
        dev_intro = await self.system_bus.introspect(BLUEZ, self.device_path)
        dev_obj = self.system_bus.get_proxy_object(BLUEZ, self.device_path, dev_intro)
        dev_props = dev_obj.get_interface("org.freedesktop.DBus.Properties")
        name_variant = await dev_props.call_get("org.bluez.Device1", "Name")
        self.name = name_variant.value

        player_intro = await self.system_bus.introspect(BLUEZ, self.player_path)
        player_obj = self.system_bus.get_proxy_object(BLUEZ, self.player_path, player_intro)
        self.player_iface = player_obj.get_interface("org.bluez.MediaPlayer1")
        self.player_props_iface = player_obj.get_interface("org.freedesktop.DBus.Properties")
        # Browsing (MediaFolder1) is optional: iOS doesn't always offer it
        # when it connects -- seen live on a cold boot, where player0 had only
        # MediaPlayer1 for minutes. Playback control and now-playing work
        # without it; only the library sync needs it, and that runs once
        # MediaFolder1 shows up (see on_interfaces_added).
        try:
            self.folder_iface = player_obj.get_interface("org.bluez.MediaFolder1")
        except InterfaceNotFoundError:
            self.folder_iface = None

    @property
    def can_browse(self) -> bool:
        return self.folder_iface is not None

    async def browse_state(self) -> tuple[str, int]:
        """(folder name, item count) as BlueZ reports them."""
        props = await self.player_props_iface.call_get_all("org.bluez.MediaFolder1")
        return props["Name"].value, props["NumberOfItems"].value

    async def wait_until_browsable(self, timeout: float = BROWSE_READY_TIMEOUT_S):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            _, count = await self.browse_state()
            if count > 0:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"phone browsing still empty after {timeout}s")
            await asyncio.sleep(0.5)

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

    async def crawl_songs(self) -> list:
        """Navigate Filesystem > My Music > Songs and list every track (title
        only, per AVRCP limits).

        The AVRCP browse position lives on the phone, and ChangeFolder can only
        move one level down at a time (BlueZ rejects a jump back to root with
        "Invalid arguments", confirmed live). Where we are has gone wrong two
        ways before: a position we remembered ourselves went stale across a
        reboot (and the top-level folders got stored as songs), and BlueZ's own
        MediaFolder1.Name went stale too -- when the phone switches apps BlueZ
        recreates the player and its Name resets to "/Filesystem" while the
        phone is still sitting in Songs (seen 2026-10-09; the crawl then looked
        for "My Music" in a list of 910 songs and failed). So decide from what
        the current folder actually holds: folders mean go deeper (Songs if
        it's listed, else My Music), playable tracks mean we're in Songs.

        Tracks are only trusted in Songs, or under BlueZ's stale "/Filesystem"
        name (the app-switch case above): a playlist or the NowPlaying list
        also holds tracks, and must never be taken for the library. Anywhere
        Songs can't be reached from raises BrowseLost."""
        name, _ = await self.browse_state()
        if name.startswith("/NowPlaying"):
            raise BrowseLost(f"browse cursor is in {name}")
        for _ in range(3):
            items = await _dbus_retry(lambda: self.folder_iface.call_list_items({}))
            folders, tracks = {}, []
            for path, props in items.items():
                name = props["Name"].value if "Name" in props else ""
                kind = props["Type"].value if "Type" in props else None
                if kind == "folder":
                    folders[name] = path
                else:
                    tracks.append({"item_path": path, "title": name or None})
            if not folders:
                name, _ = await self.browse_state()
                if not (name.endswith("/Songs") or name == "/Filesystem"):
                    raise BrowseLost(f"found tracks in {name}, not Songs")
                return tracks
            target = next((path for name, path in folders.items() if name.endswith("Songs")), None) \
                or next((path for name, path in folders.items() if name.endswith("My Music")), None)
            if target is None:
                raise BrowseLost(f"can't find My Music or Songs among {sorted(folders)}")
            await _dbus_retry(lambda: self.folder_iface.call_change_folder(target))
        raise RuntimeError("still not in a list of songs after navigating three levels")

    async def reconnect(self):
        """Drop and re-make the Bluetooth connection: a fresh AVRCP session
        starts browsing at the root, the only reliable way back there."""
        intro = await self.system_bus.introspect(BLUEZ, self.device_path)
        dev = self.system_bus.get_proxy_object(BLUEZ, self.device_path, intro).get_interface("org.bluez.Device1")
        await dev.call_disconnect()
        await asyncio.sleep(3)
        await asyncio.wait_for(dev.call_connect(), 30)

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
    # This service is the source connection manager: it decides when the
    # library gets synced, jb-library just stores whatever we hand it. Sync
    # once per player object -- a new connection, or an AVRCP renegotiation
    # (BlueZ recreates the player, which invalidates every item_path from the
    # previous crawl). Never on a timer, never with no phone connected.
    synced_player_path: str | None = None
    sync_lock = asyncio.Lock()
    # The last crawl, in the phone's order: lets a pick whose play path went
    # stale be found again in a fresh crawl (see replay_after_resync).
    last_tracks: list = []
    # Latest now-playing status, so a browse reset never cuts off music.
    playing = False
    last_reset_at = None      # loop time of the last browse reset
    reset_pending = False
    # Browse lease: while jb-crawl holds it, this service doesn't browse.
    lease: dict | None = None  # {"holder", "until", "revoked_at"}
    pending_pick: dict | None = None  # a pick that arrived while leased

    def now() -> float:
        return asyncio.get_running_loop().time()

    async def sync_library(bus: Bus):
        nonlocal synced_player_path, last_tracks
        async with sync_lock:
            p = phone
            if p is None:
                log.warning("library sync requested with no phone connected")
                return
            if not p.can_browse:
                log.warning("library sync requested but the phone isn't offering browsing yet")
                return
            if lease:
                log.info("library sync skipped: %s holds the browse lease", lease["holder"])
                return
            await p.wait_until_browsable()
            tracks = await asyncio.wait_for(p.crawl_songs(), CRAWL_TIMEOUT_S)
            if phone is not p:
                # Superseded mid-crawl by a reconnect/renegotiation -- these
                # item_paths are already dead. The new player's own sync
                # (triggered from connect_current_phone) replaces them.
                log.info("discarding crawl from superseded player %s", p.player_path)
                return
            # Retained so jb-library gets it no matter which service started
            # first (e.g. phone already connected at boot).
            await bus.publish(
                f"jukebox/{SERVICE_NAME}/crawl-result",
                {"mac": p.mac, "tracks": tracks},
                retain=True,
            )
            synced_player_path = p.player_path
            last_tracks = tracks
            log.info("library sync complete: %d tracks from %s", len(tracks), p.name)

    async def sync_library_logged(bus: Bus):
        # Fired as a background task -- log failures instead of letting them
        # vanish with the unawaited task. Retries a failed sync a few times;
        # lost browsing goes straight to a browse reset instead.
        for attempt, delay in enumerate((0, *SYNC_RETRY_DELAYS_S), start=1):
            await asyncio.sleep(delay)
            try:
                await sync_library(bus)
                return
            except BrowseLost as e:
                log.warning("library sync: browsing is lost (%s); resetting the browse session", e)
                request_browse_reset(bus)
                return
            except Exception:
                log.exception("library sync failed (attempt %d/%d)", attempt, 1 + len(SYNC_RETRY_DELAYS_S))
        # Out of retries: a reset (reconnect) gives the phone a fresh browse
        # session, rather than leaving picks broken until the next connect.
        log.error("library sync gave up; resetting the browse session")
        request_browse_reset(bus)

    def request_browse_reset(bus: Bus):
        nonlocal reset_pending
        if not reset_pending:
            reset_pending = True
            asyncio.create_task(browse_reset(bus))

    async def browse_reset(bus: Bus):
        """Reconnect the phone to get a fresh browse session at the root;
        the reconnect's own player events then trigger a normal sync. Waits
        while music plays (a reconnect would cut it off) and keeps resets at
        least RESET_MIN_INTERVAL_S apart."""
        nonlocal reset_pending, last_reset_at
        try:
            while True:
                if phone is None:
                    log.info("browse reset dropped: no phone connected")
                    return
                if lease:
                    log.info("browse reset dropped: %s holds the browse lease", lease["holder"])
                    return
                wait = 0.0 if last_reset_at is None else last_reset_at + RESET_MIN_INTERVAL_S - now()
                if playing:
                    log.info("browse reset waiting: music is playing")
                    await asyncio.sleep(RESET_WAIT_POLL_S)
                elif wait > 0:
                    log.info("browse reset waiting %.0fs (rate limit)", wait)
                    await asyncio.sleep(wait)
                else:
                    break
            last_reset_at = now()
            log.info("browse reset: reconnecting %s", phone.name)
            await phone.reconnect()
        except Exception:
            log.exception("browse reset failed")
        finally:
            reset_pending = False

    async def connect_current_phone(bus: Bus, retries: int = 5, retry_delay: float = 0.8):
        nonlocal phone, synced_player_path
        found = await find_connected_player(system_bus)
        if not found:
            phone = None
            synced_player_path = None
            await bus.publish(f"jukebox/{SERVICE_NAME}/connected", {"connected": False}, retain=True)
            # Drop the retained crawl too, so a restarting jb-library (or a
            # reboot, since the broker persists retained messages) never
            # ingests item_paths from a player that no longer exists.
            await bus.clear_retained(f"jukebox/{SERVICE_NAME}/crawl-result")
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
            if not phone.can_browse:
                log.info("phone has no browsing yet -- library sync will run when it appears")
            elif phone.player_path != synced_player_path:
                asyncio.create_task(sync_library_logged(bus))
            return
        log.error("phone setup failed after %d attempts, giving up", retries)
        phone = None
        await bus.publish(f"jukebox/{SERVICE_NAME}/connected", {"connected": False}, retain=True)

    async def publish_lease(bus: Bus, **extra):
        state = {"holder": lease["holder"] if lease else None,
                 "player_path": phone.player_path if lease and phone else None,
                 "revoked": bool(lease and lease.get("revoked_at")), **extra}
        await bus.publish(f"jukebox/{SERVICE_NAME}/lease", state, retain=True)

    async def grant_lease(bus: Bus, cmd: dict):
        """Browse lease request (jb-crawl). Granted only when the library is
        synced and nothing else is browsing; the answer is the retained
        jukebox/jb-source-phone/lease state (holder = who has it)."""
        nonlocal lease
        holder = cmd.get("holder") or "?"
        reason = None
        if lease:
            reason = f"held by {lease['holder']}"
        elif phone is None or not phone.can_browse:
            reason = "no browsable phone"
        elif sync_lock.locked() or reset_pending:
            reason = "library sync or browse reset in progress"
        elif phone.player_path != synced_player_path:
            reason = "library not synced yet"
        if reason:
            log.info("browse lease for %s denied: %s", holder, reason)
            await publish_lease(bus, denied=holder, reason=reason)
            return
        ttl = min(float(cmd.get("ttl_s", LEASE_MAX_S)), LEASE_MAX_S)
        lease = {"holder": holder, "until": now() + ttl, "revoked_at": None}
        log.info("browse lease granted to %s for %.0fs", holder, ttl)
        await publish_lease(bus)

    async def end_lease(bus: Bus, why: str):
        """The lease is over (released, revoked and handed back, or timed
        out): take browsing back -- re-sync Songs, then play any pick that
        came in meanwhile."""
        nonlocal lease, pending_pick
        if not lease:
            return
        log.info("browse lease from %s ended (%s); re-syncing", lease["holder"], why)
        lease = None
        await publish_lease(bus)
        await sync_library_logged(bus)
        pick, pending_pick = pending_pick, None
        if pick:
            await handle_command(bus, pick)

    async def watch_lease(bus: Bus):
        while True:
            await asyncio.sleep(LEASE_CHECK_S)
            if not lease:
                continue
            if lease["revoked_at"] and now() - lease["revoked_at"] > LEASE_REVOKE_WAIT_S:
                await end_lease(bus, f"{lease['holder']} didn't hand back after a pick")
            elif now() > lease["until"]:
                await end_lease(bus, "timed out")

    async def handle_command(bus: Bus, cmd: dict):
        nonlocal phone, pending_pick
        action = cmd.get("action")
        if action == "refresh_connection":
            await connect_current_phone(bus)
            return
        if action == "lease_request":
            await grant_lease(bus, cmd)
            return
        if action == "lease_release":
            if lease and cmd.get("holder") == lease["holder"]:
                asyncio.create_task(end_lease(bus, "released"))
            return
        if phone is None:
            log.warning("command %r received with no phone connected", action)
            return
        if action == "play_item" and lease:
            # A pick always wins over a deep crawl: revoke, and play once the
            # holder has handed browsing back (end_lease re-syncs first, so
            # the pick's stale path gets replayed by title).
            pending_pick = cmd
            if not lease["revoked_at"]:
                lease["revoked_at"] = now()
                log.info("pick while %s holds the browse lease; revoking", lease["holder"])
                await publish_lease(bus)
            return
        if action in ("play", "pause", "next", "previous"):
            await phone.transport(action)
        elif action == "play_item":
            try:
                await phone.play_item(cmd["item_path"])
            except Exception:
                log.exception("play_item failed: %r", cmd["item_path"])
                if not await replay_after_resync(bus, cmd):
                    await bus.publish(f"jukebox/{SERVICE_NAME}/play-error", cmd)
        elif action == "crawl":
            # Manual re-sync (e.g. from the phone manager UI, after adding
            # music on the phone). Automatic syncs happen on connect.
            await sync_library(bus)
        else:
            log.warning("unknown command action: %r", action)

    async def replay_after_resync(bus: Bus, cmd: dict) -> bool:
        """A pick failed, most likely because its play path belongs to a
        player BlueZ has since replaced (the phone switched apps and the
        re-sync hasn't happened or was missed). Re-sync now, find the same
        song in the fresh crawl, and play that -- the pick plays a few
        seconds late instead of being dropped. Returns whether it played."""
        stale = cmd["item_path"]
        old = [t for t in last_tracks if t["item_path"] == stale]
        title = old[0]["title"] if old else cmd.get("title")
        if not title:
            return False
        # Which copy of the title (the library keeps same-title songs apart).
        copy = 0
        if old:
            copy = sum(1 for t in last_tracks[:last_tracks.index(old[0])] if t["title"] == title)
        log.info("play path looks stale; re-syncing to retry %r", title)
        try:
            await sync_library(bus)
        except BrowseLost as e:
            log.warning("re-sync for a stale play path: browsing is lost (%s)", e)
            request_browse_reset(bus)
            return False
        except Exception:
            log.exception("re-sync for a stale play path failed")
            return False
        fresh = [t["item_path"] for t in last_tracks if t["title"] == title]
        if not fresh or phone is None:
            return False
        path = fresh[min(copy, len(fresh) - 1)]
        if path == stale:
            return False  # the path was current; something else is wrong
        try:
            await phone.play_item(path)
        except Exception:
            log.exception("retry after re-sync failed: %r", path)
            return False
        log.info("played %r after re-sync", title)
        return True

    async def poll_now_playing(bus: Bus):
        # Publish only on change. While playing, position_ms moves every
        # poll so the progress bar still updates; paused/idle goes quiet.
        nonlocal playing
        last_np = None
        while True:
            if phone is None:
                last_np = None  # republish right away on the next connection
                playing = False
            else:
                try:
                    np = await phone.now_playing()
                    playing = np.get("status") == "playing"
                    if np != last_np:
                        last_np = np
                        await bus.publish(f"jukebox/{SERVICE_NAME}/now-playing", np)
                except Exception:
                    log.exception("failed to read now-playing state")
            await asyncio.sleep(2)

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])

        # Subscribe before publishing "connected" -- otherwise another
        # service reacting to that retained message (e.g. jb-core sending a
        # play_item on reconnect) can race ahead of our own subscription and
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
            elif "org.bluez.MediaFolder1" in interfaces:
                # Browsing arrived later on an already-connected player --
                # re-set up so the Phone picks it up and syncs the library.
                log.info("MediaFolder1 appeared at %s, re-attaching for library sync", path)
                asyncio.create_task(connect_current_phone(bus))

        def on_interfaces_removed(path, interfaces):
            nonlocal synced_player_path
            # BlueZ reuses player names (player1 can go away and come back
            # as a brand-new object), so a name that's gone is no longer
            # "synced" -- whatever appears next under it needs its own sync.
            if "org.bluez.MediaPlayer1" in interfaces and str(path) == synced_player_path:
                synced_player_path = None
            if phone is not None and "org.bluez.MediaPlayer1" in interfaces and str(path) == phone.player_path:
                log.info("MediaPlayer1 disappeared at %s", path)
                asyncio.create_task(connect_current_phone(bus))

        om.on_interfaces_added(on_interfaces_added)
        om.on_interfaces_removed(on_interfaces_removed)

        # No lease survives a restart: whoever held one must ask again.
        await publish_lease(bus)

        asyncio.create_task(bus.run_heartbeat())
        asyncio.create_task(poll_now_playing(bus))
        asyncio.create_task(watch_lease(bus))
        async for _topic, payload in messages:
            try:
                await handle_command(bus, payload)
            except Exception:
                log.exception("failed to handle command: %r", payload)


if __name__ == "__main__":
    asyncio.run(main())
