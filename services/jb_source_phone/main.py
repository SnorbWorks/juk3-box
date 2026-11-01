import asyncio
import json
import time
from pathlib import Path

from dbus_next import BusType, Variant
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError, InterfaceNotFoundError

from shared import config, logging as jb_logging
from shared.bus import Bus
from shared.files import write_atomic
from services.jb_source_phone import phones as ph
from services.jb_source_phone import reconnect as rc
from services.jb_source_phone import songs as sg
from services.jb_source_phone.pairing import Pairing

SERVICE_NAME = "jb-source-phone"
BLUEZ = "org.bluez"

# Right after an AVRCP renegotiation (BlueZ tearing down the old player
# object and standing up a new one), the browsing channel can still be
# settling and answers with a transient "Device or resource busy" -- retry
# rather than leaving the caller (jb-library's crawl) with a stale result.
# Bluetooth on/off (the cabinet's Settings, the admin): remembered across
# restarts. Off, the jukebox is invisible and no phone can connect.
BLUETOOTH_STATE = Path("/var/lib/juk3-box/bluetooth.json")


def bluetooth_wanted() -> bool:
    try:
        return bool(json.loads(BLUETOOTH_STATE.read_text()).get("on", True))
    except (OSError, ValueError, AttributeError):
        return True


def remember_bluetooth(on: bool) -> None:
    write_atomic(BLUETOOTH_STATE, json.dumps({"on": on}))


DBUS_RETRY_ATTEMPTS = 5
DBUS_RETRY_DELAY_S = 0.5
BUSY_WAIT_S = 60
PLAY_BUSY_WAIT_S = 60     # a pick waits this long for a busy BlueZ (a library sync takes 20-50 s)

# A whole library crawl normally takes well under a minute. BlueZ calls have
# no timeout of their own, and a crawl that hung when the phone switched
# apps mid-way held the sync lock forever, so no later sync ever ran (seen
# 2026-10-09). Give up and let the next player change try again.
CRAWL_TIMEOUT_S = 120
# A full read lists Songs this many at a time (one BlueZ call each, ~1.5 s
# for 100 songs), so the screen can say how far along it is.
READ_CHUNK = 100
# How much of a folder is looked at to find our way to Songs: the root and
# My Music hold a handful of folders.
PEEK = 30

# Right after a connect the phone's browse root reports 0 items for a moment
# and answers "busy"; a sync started then fails (seen 2026-10-11). Wait for
# the folder to report items first. A root still empty after this is reset
# (a reconnect) at once: on 2026-10-29 it stayed empty for two minutes of
# retries, and the reset fixed it in 6 s.
BROWSE_READY_TIMEOUT_S = 8
# A player but no browsing this long: stop "connecting" and ask for a song
# in Music. Browsing normally comes within ~1 s of the player (every
# connect since Oct 28). When it doesn't, iOS is holding it back (the app
# that last played isn't Music) and only playing a song in Music brings
# it: waiting a minute before saying so only kept the overlay up.
NO_BROWSE_S = 6
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
# A pick the phone can't take right now (it stopped answering, or offers no
# controls at all) is kept while the cabinet reconnects the phone, and
# played once it's back -- or given up on after WAITING_PICK_S. The reconnect
# is rate-limited separately from browse resets: a pick is worth it.
PICK_RECONNECT_MIN_S = 120
WAITING_PICK_S = 90


MUSIC_APP = "Music"


class BrowseLost(RuntimeError):
    """The phone's browse cursor is somewhere the Songs list can't be reached
    from (Playlists, NowPlaying, deep in another branch)."""


class BrowseEmpty(BrowseLost):
    """The browse root reports no items: a fresh session (a reset) fixes it."""


class NotListed(RuntimeError):
    """A play path BlueZ has no song for: not listed in this connection."""


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
        self.app = None           # the app the phone is presenting ("Music", "Messages"...)

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
        try:
            self.app = (await self.player_props_iface.call_get("org.bluez.MediaPlayer1", "Name")).value or None
        except DBusError:
            self.app = None
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

    @property
    def wrong_app(self) -> bool:
        """iOS hands the controls to whichever app last made a sound. When
        that's Messages (a voice message, 2026-10-19) there's no song list and
        nothing plays, and no reconnect helps: only playing something in
        Music does."""
        return not self.can_browse and self.app is not None and self.app != MUSIC_APP

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
                raise BrowseEmpty(f"phone browsing still empty after {timeout}s")
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

    async def list_range(self, start: int, end: int) -> list:
        """Songs start..end (inclusive, by place) of the current folder:
        one BlueZ call. BlueZ fetches just that range from the phone, about
        four songs per round trip, and replies once it has them all."""
        items = await _dbus_retry(lambda: self.folder_iface.call_list_items(
            {"Start": Variant("u", start), "End": Variant("u", end)}))
        out = []
        for path, props in items.items():
            if sg.is_folder(props):
                raise BrowseLost(f"found a folder at {start}-{end} of what should be Songs")
            out.append(sg.track_from_item(path, props))
        return out

    async def goto_songs(self) -> int:
        """Moves the browse cursor into Songs (Filesystem > My Music >
        Songs), without listing it. Returns how many songs it holds.

        The AVRCP browse position lives on the phone, and ChangeFolder can only
        move one level down at a time (BlueZ rejects a jump back to root with
        "Invalid arguments", confirmed live). Where we are has gone wrong two
        ways before: a position we remembered ourselves went stale across a
        reboot (and the top-level folders got stored as songs), and BlueZ's own
        MediaFolder1.Name went stale too -- when the phone switches apps BlueZ
        recreates the player and its Name resets to "/Filesystem" while the
        phone is still sitting in Songs (seen 2026-10-09; the crawl then looked
        for "My Music" in a list of 910 songs and failed). So decide from what
        the current folder actually holds (its first few items): folders mean
        go deeper (Songs if it's listed, else My Music), songs mean we're in
        Songs.

        Songs are only trusted in Songs, or under BlueZ's stale "/Filesystem"
        name (the app-switch case above): a playlist or the NowPlaying list
        also holds songs, and must never be taken for the library. Anywhere
        Songs can't be reached from raises BrowseLost."""
        name, count = await self.browse_state()
        if name.startswith("/NowPlaying"):
            raise BrowseLost(f"browse cursor is in {name}")
        for _ in range(3):
            if name.endswith("/Songs"):
                return count
            items = await _dbus_retry(lambda: self.folder_iface.call_list_items(
                {"Start": Variant("u", 0), "End": Variant("u", PEEK - 1)}))
            folders, songs_here = {}, 0
            for path, props in items.items():
                if sg.is_folder(props):
                    folders[props["Name"].value if "Name" in props else ""] = path
                else:
                    songs_here += 1
            if not folders:
                if songs_here and name == "/Filesystem":
                    return count          # in Songs under the stale name
                raise BrowseLost(f"found {songs_here} songs and no folders in {name}, not Songs")
            target = next((path for n, path in folders.items() if n.endswith("Songs")), None) \
                or next((path for n, path in folders.items() if n.endswith("My Music")), None)
            if target is None:
                raise BrowseLost(f"can't find My Music or Songs among {sorted(folders)}")
            await _dbus_retry(lambda: self.folder_iface.call_change_folder(target))
            name, count = await self.browse_state()
        raise RuntimeError("still not in a list of songs after navigating three levels")

    async def check_songs(self, stored: list, count: int) -> list | None:
        """The stored list (songs.py), with this connection's play paths, if
        the phone still holds the same songs: the same number, and the same
        song at each spot check (one round trip each). None: read them all."""
        if not stored or count != len(stored):
            return None
        folder = None
        for i in sg.spot_indices(count):
            listed = await self.list_range(i, i)
            if not listed or not sg.same_song(stored[i], listed[0]):
                return None
            folder = sg.folder_of(listed[0]["item_path"])
        return sg.rebased(stored, folder)

    async def read_songs(self, count: int, progress=None) -> list:
        """Every song in Songs, READ_CHUNK at a time, calling progress(done,
        total) after each chunk. A count of 0 means BlueZ doesn't know it:
        one unbounded listing then, as before."""
        if count <= 0:
            items = await _dbus_retry(lambda: self.folder_iface.call_list_items({}))
            return [sg.track_from_item(path, props) for path, props in items.items() if not sg.is_folder(props)]
        tracks: list = []
        while len(tracks) < count:
            start = len(tracks)
            listed = await self.list_range(start, min(start + READ_CHUNK, count) - 1)
            if not listed:
                break             # the phone has fewer than it said (out of range)
            tracks += listed
            if progress:
                await progress(len(tracks), count)
        return tracks

    async def reconnect(self):
        """Drop and re-make the Bluetooth connection: a fresh AVRCP session
        starts browsing at the root, the only reliable way back there."""
        await rc.reconnect(self.system_bus, self.device_path)

    async def play_item(self, item_path: str):
        item_intro = await self.system_bus.introspect(BLUEZ, item_path)
        item_obj = self.system_bus.get_proxy_object(BLUEZ, item_path, item_intro)
        try:
            item_iface = item_obj.get_interface("org.bluez.MediaItem1")
        except InterfaceNotFoundError:
            raise NotListed(item_path) from None
        # "Busy" means BlueZ is still fetching a folder listing from the phone
        # (a library sync running): wait it out, it isn't a stale path. A pick
        # made during a sync got "busy", was taken for stale, and the re-sync
        # piled on top knocked the phone's controls off (2026-10-19).
        deadline = asyncio.get_running_loop().time() + PLAY_BUSY_WAIT_S
        while True:
            try:
                await item_iface.call_play()
                return
            except DBusError as e:
                if "busy" not in str(e).lower() or asyncio.get_running_loop().time() >= deadline:
                    raise
            await asyncio.sleep(0.5)

    async def transport(self, action: str):
        method = {
            "play": self.player_iface.call_play,
            "pause": self.player_iface.call_pause,
            "next": self.player_iface.call_next,
            "previous": self.player_iface.call_previous,
        }[action]
        await method()


async def find_connected_player(system_bus, prefer_mac: str | None = None) -> tuple[str, str] | None:
    """The connected phone's (device path, player path). For a moment two
    phones can be connected (one about to be turned away, see phones.py):
    the one with the jukebox wins."""
    intro = await system_bus.introspect(BLUEZ, "/")
    obj = system_bus.get_proxy_object(BLUEZ, "/", intro)
    om = obj.get_interface("org.freedesktop.DBus.ObjectManager")
    objects = await om.call_get_managed_objects()
    found = []
    for path, interfaces in objects.items():
        if "org.bluez.MediaPlayer1" in interfaces:
            device_path = str(path).rsplit("/player", 1)[0]
            found.append((device_path, str(path)))
    if prefer_mac:
        wanted = "dev_" + prefer_mac.replace(":", "_")
        for device_path, player_path in found:
            if device_path.endswith(wanted):
                return device_path, player_path
    return found[0] if found else None


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
    # A pick whose play path went stale while the phone offered no browsing
    # (iOS reports the last app that made a sound, e.g. Messages, as its
    # player and won't browse it): played once a reconnect's sync lands.
    waiting_pick: dict | None = None
    last_pick_reconnect_at = None
    # Where the phone's song list stands, for jb-core (picks wait until it's
    # "ready") and the screen (its "updating library" overlay): none,
    # connecting, syncing, ready, failed, wrong_app, no_browse (connecting
    # gave up: a player but no browsing for NO_BROWSE_S; the screen says to
    # play a song in Music, and it clears when browsing appears). A pick
    # sent before the list is read in this connection can't play (2026-10-19).
    # A full read also says how far along it is (done, total).
    sync_state = {"state": None, "sent": None}
    # While jb-crawl holds the browse lease, and through the re-sync after
    # it, the song list stays "ready" on the bus: the crawl's own reconnects
    # and the re-sync are background work, and a pick revokes the lease and
    # plays a few seconds late (handle_command). Before, they put the
    # "updating library" overlay up for a minute and more, and a waiting
    # pick behind it (2026-11-01).
    quiet = {"on": False, "why": None}
    # Song ID -> (place in last_tracks, play path), for picks (play_pick).
    by_uid: dict = {}
    # The sync running now, so a burst of player events (a connect raises
    # three or four) starts one sync, not one each (2026-10-29: two retry
    # loops ran side by side for two minutes).
    sync_task: dict = {"task": None, "player": None}

    def now() -> float:
        return asyncio.get_running_loop().time()

    async def publish_sync(bus: Bus, state: str, **extra):
        # Done, failed or the phone gone: shown, and the quiet is over.
        if quiet["on"] and state not in ("ready", "failed", "none"):
            if sync_state["state"] != state:
                log.info("song list: %s (not shown: %s)", state, quiet["why"])
            sync_state["state"] = state
            return
        quiet["on"] = False
        payload = {"state": state, "name": phone.name if phone else None,
                   "mac": getattr(phone, "mac", None), **extra}
        if sync_state["sent"] == payload:
            sync_state["state"] = state
            return
        if (sync_state["sent"] or {}).get("state") != state:
            log.info("song list: %s", state)
        sync_state["state"], sync_state["sent"] = state, payload
        await bus.publish(f"jukebox/{SERVICE_NAME}/sync", payload, retain=True)

    async def stop_waiting_for_browse(bus: Bus, p):
        """iOS may never offer browsing until Music plays (fresh v0.1.9 card,
        2026-10-28): after NO_BROWSE_S of "connecting", stop the spinner and
        say what to do. Browsing appearing reruns connect_current_phone,
        which syncs as usual. (By the phone, not the Phone object: a burst of
        player events makes a new one each time.)"""
        await asyncio.sleep(NO_BROWSE_S)
        if phone is not None and phone.mac == p.mac and sync_state["state"] == "connecting" \
                and not phone.can_browse:
            log.info("still no browsing after %ds (the phone presents %r): asking for a song in Music",
                     NO_BROWSE_S, phone.app)
            await publish_sync(bus, "no_browse")

    def same_player(p) -> bool:
        """Whether p is still the phone's player. By path, not object: every
        event of a connect's burst sets up a new Phone for the same player,
        and a read compared by object was thrown away for the next one's
        (2-35 s more of the overlay: Oct 30 x2, Oct 31)."""
        return phone is not None and phone.player_path == p.player_path

    def sync_running() -> bool:
        task = sync_task["task"]
        return task is not None and not task.done() and phone is not None \
            and sync_task["player"] == phone.player_path

    def start_sync(bus: Bus, saved: bool = False):
        """Sync the current phone in the background, unless that's already
        under way. saved: show the list kept from last time first (show_saved)."""
        if sync_running():
            return
        sync_task["player"] = phone.player_path
        sync_task["task"] = asyncio.create_task(sync_library_logged(bus, saved))

    async def show_saved(bus: Bus, p) -> bool:
        """A reconnect with a list kept from last time (songs.py): the board
        gets it, and the song list is "ready", at once -- the overlay comes
        down a second after the phone connects. Getting to Songs to check
        the list takes ~20 s on BlueZ 5.82 (two folder changes, each
        stalling 10 s), so the check runs after, out of sight (quiet), and
        a pick made meanwhile waits for it (play_at; jb-core's pick doctor
        says "One moment"). Returns whether there was a list to show."""
        nonlocal last_tracks, by_uid
        saved = sg.load(p.mac)
        if not saved["songs"]:
            return False
        tracks = sg.rebased(saved["songs"], p.player_path + saved["under_player"])
        await bus.publish(f"jukebox/{SERVICE_NAME}/crawl-result", {"mac": p.mac, "tracks": tracks}, retain=True)
        last_tracks = tracks
        by_uid = {uid: (i, tracks[i]["item_path"]) for uid, i in sg.positions(tracks).items()}
        log.info("showing the %d songs kept from last time; checking them now", len(tracks))
        await publish_sync(bus, "ready")
        quiet["on"], quiet["why"] = True, "checking the kept song list"
        return True

    async def read_or_check(p, full: bool, bus: Bus) -> tuple[list, str]:
        """Into Songs, then the stored list if a spot check says it's still
        right (songs.py), else a full read. Returns (tracks, how)."""
        start_name, start_count = await p.browse_state()
        started = now()
        count = await p.goto_songs()
        log.info("browsing: into Songs (%d songs) in %.1fs, from %s (%d items)",
                 count, now() - started, start_name, start_count)
        if not full:
            started = now()
            tracks = await p.check_songs(sg.load(p.mac)["songs"], count)
            log.info("spot check of the kept list: %s in %.1fs",
                     "same songs" if tracks is not None else "changed (or none kept)", now() - started)
            if tracks is not None:
                return tracks, "checked"

        async def progress(done: int, total: int):
            if same_player(p):
                await publish_sync(bus, "syncing", done=done, total=total)
        tracks = await p.read_songs(count, progress)
        return tracks, "read"

    async def sync_library(bus: Bus, final: bool = True, full: bool = False, saved: bool = False):
        """Read the phone's Songs list: a spot check of the stored one when
        it's there (a couple of seconds), else every song (20-45 s). full:
        read every song whatever the check says (a pick's song wasn't where
        the list said, or a re-sync asked for from the admin). final: a
        failure here is the last try (sync_library_logged retries quietly
        first). saved: a connect -- show the list kept from last time while
        it's checked (show_saved)."""
        nonlocal synced_player_path, last_tracks, by_uid
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
            if not (saved and await show_saved(bus, p)):
                await publish_sync(bus, "syncing")
            started = now()
            try:
                await p.wait_until_browsable()
                tracks, how = await asyncio.wait_for(read_or_check(p, full, bus), CRAWL_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not same_player(p):
                    # The player went away mid-read (a connect's burst, a
                    # drop): the new one's own sync covers it. Not a failure
                    # to retry -- a retry was a second sync after it.
                    log.info("sync of superseded player %s stopped (%s)", p.player_path, e)
                    return
                if final:
                    await publish_sync(bus, "failed")
                raise
            if not same_player(p):
                # Superseded mid-crawl by a reconnect/renegotiation -- these
                # item_paths are already dead. The new player's own sync
                # (triggered from connect_current_phone) replaces them.
                log.info("discarding crawl from superseded player %s", p.player_path)
                return
            if how == "read":
                try:
                    if sg.save(p.mac, tracks, p.player_path):
                        log.info("kept the song list for %s's next connect", p.name)
                except OSError:
                    log.exception("couldn't keep the song list")
            # Retained so jb-library gets it no matter which service started
            # first (e.g. phone already connected at boot).
            await bus.publish(
                f"jukebox/{SERVICE_NAME}/crawl-result",
                {"mac": p.mac, "tracks": tracks},
                retain=True,
            )
            synced_player_path = p.player_path
            last_tracks = tracks
            by_uid = {uid: (i, tracks[i]["item_path"]) for uid, i in sg.positions(tracks).items()}
            log.info("library sync complete: %d tracks from %s (%s in %.1fs)", len(tracks), p.name,
                     "spot-checked the stored list" if how == "checked" else "read every song", now() - started)
            await publish_sync(bus, "ready")
        if waiting_pick:
            asyncio.create_task(play_waiting_pick(bus))

    async def play_waiting_pick(bus: Bus):
        nonlocal waiting_pick
        pick, waiting_pick = waiting_pick, None
        if pick and not await play_fresh_copy(pick):
            await bus.publish(f"jukebox/{SERVICE_NAME}/play-error", pick["cmd"] | {"reason": "not_found"})

    async def pick_problem(bus: Bus, cmd: dict, reason: str | None, retrying: bool):
        """What went wrong with a pick, for jb-core's pick doctor."""
        await bus.publish(f"jukebox/{SERVICE_NAME}/pick-problem",
                          {"title": cmd.get("title"), "item_path": cmd.get("item_path"),
                           "reason": reason, "retrying": retrying, "app": phone.app if phone else None})

    async def wait_for_phone(bus: Bus, pick: dict, reason: str):
        """Keep the pick, reconnect the phone, and play it after the
        reconnect's sync (sync_library) -- or give up after WAITING_PICK_S."""
        nonlocal waiting_pick, last_pick_reconnect_at
        waiting_pick = pick
        await pick_problem(bus, pick["cmd"], reason, retrying=True)

        async def give_up():
            nonlocal waiting_pick
            await asyncio.sleep(WAITING_PICK_S)
            if waiting_pick is pick:
                waiting_pick = None
                log.warning("gave up waiting to play %r", pick["title"])
                await bus.publish(f"jukebox/{SERVICE_NAME}/play-error",
                                  {**pick["cmd"], "reason": "no_controls" if phone is None else reason})
        asyncio.create_task(give_up())

        if reason == "wrong_app":
            log.info("keeping %r until the phone presents Music (it's on %r)", pick["title"], phone.app if phone else None)
            return
        if last_pick_reconnect_at is not None and now() - last_pick_reconnect_at < PICK_RECONNECT_MIN_S:
            log.info("not reconnecting for %r: reconnected for a pick moments ago", pick["title"])
            return
        audio = [d for d in await rc.phones(system_bus) if d[3]]
        device = next((d for d in audio if d[0] == lock.owner), audio[0] if audio else None)
        if device is None:
            return
        mac, path = device[0], device[1]
        last_pick_reconnect_at = now()
        log.info("reconnecting the phone to play %r (%s)", pick["title"], reason)
        # Our own drop (drop_link marks it leaving, so the watcher doesn't take
        # it for the owner's choice and give up), then the usual retrying
        # reconnect: one attempt alone failed or hung (2026-10-19).
        try:
            await drop_link(mac, path)
        except Exception:
            log.exception("disconnect for a pick failed")
        await asyncio.sleep(3)
        start_reconnect(bus, "pick")

    async def sync_library_logged(bus: Bus, saved: bool = False):
        # Fired as a background task -- log failures instead of letting them
        # vanish with the unawaited task. Retries a failed sync a few times;
        # lost browsing goes straight to a browse reset instead.
        delays = (0, *SYNC_RETRY_DELAYS_S)
        me = asyncio.current_task()
        for attempt, delay in enumerate(delays, start=1):
            await asyncio.sleep(delay)
            newer = sync_task["task"]
            if attempt > 1 and newer is not None and newer is not me and not newer.done():
                log.info("library sync retry dropped: a newer sync is under way")
                return
            try:
                await sync_library(bus, final=attempt == len(delays), saved=saved and attempt == 1)
                return
            except BrowseLost as e:
                log.warning("library sync: browsing is lost (%s); resetting the browse session", e)
                # An empty root with a reset free to run now: the reset is the
                # fix, a few seconds away, so the screen keeps saying "updating".
                if not (isinstance(e, BrowseEmpty) and reset_can_run_now()):
                    await publish_sync(bus, "failed")
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

    def reset_can_run_now() -> bool:
        """A browse reset wouldn't have to wait (browse_reset's rules)."""
        return not playing and not lease and phone is not None and (
            last_reset_at is None or now() - last_reset_at >= RESET_MIN_INTERVAL_S)

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
            # An empty root keeps "syncing" up through its reset (sync_library_logged):
            # with no reset coming, say it failed rather than spin on.
            if sync_state["state"] == "syncing" and not sync_running():
                await publish_sync(bus, "failed")
        finally:
            reset_pending = False

    async def connect_current_phone(bus: Bus, retries: int = 5, retry_delay: float = 0.8):
        nonlocal phone, synced_player_path
        found = await find_connected_player(system_bus, lock.owner)
        if not found:
            phone = None
            synced_player_path = None
            # Connected for audio but offering no remote control (a laptop
            # playing a video): still connected, just no songs to show.
            audio = [d for d in await rc.phones(system_bus) if d[3]]
            pick = next((d for d in audio if d[0] == lock.owner), audio[0] if audio else None)
            if pick:
                log.info("%s is connected for audio only (no song list or controls)", pick[2])
                await bus.publish(f"jukebox/{SERVICE_NAME}/connected",
                                  {"connected": True, "name": pick[2], "mac": pick[0], "controls": False}, retain=True)
            else:
                await bus.publish(f"jukebox/{SERVICE_NAME}/connected", {"connected": False}, retain=True)
            # Drop the retained crawl too, so a restarting jb-library (or a
            # reboot, since the broker persists retained messages) never
            # ingests item_paths from a player that no longer exists.
            await bus.clear_retained(f"jukebox/{SERVICE_NAME}/crawl-result")
            await publish_sync(bus, "none")
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
            try:
                book.touch(phone.mac, phone.name, time.time())
            except OSError:
                log.exception("couldn't update the phone book")
            await bus.publish(
                f"jukebox/{SERVICE_NAME}/connected",
                {"connected": True, "name": phone.name, "mac": phone.mac,
                 "app": phone.app, "browsable": phone.can_browse},
                retain=True,
            )
            if phone.wrong_app:
                log.info("phone is presenting %r, not Music: no song list until Music plays", phone.app)
                await publish_sync(bus, "wrong_app")
            elif not phone.can_browse:
                log.info("phone has no browsing yet (presenting %r) -- library sync will run when it appears",
                         phone.app)
                if sync_state["state"] != "no_browse":
                    await publish_sync(bus, "connecting")
                    asyncio.create_task(stop_waiting_for_browse(bus, phone))
            elif phone.player_path != synced_player_path:
                if not sync_running():
                    # With a list kept from last time, the sync shows it
                    # at once (show_saved): no "syncing" in between.
                    if not sg.load(phone.mac)["songs"]:
                        await publish_sync(bus, "syncing")
                    start_sync(bus, saved=True)
            else:
                await publish_sync(bus, "ready")
            return
        log.error("phone setup failed after %d attempts, giving up", retries)
        phone = None
        await bus.publish(f"jukebox/{SERVICE_NAME}/connected", {"connected": False}, retain=True)
        await publish_sync(bus, "none")

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
        quiet["on"], quiet["why"] = True, f"{holder} is browsing"
        await publish_lease(bus)

    async def end_lease(bus: Bus, why: str):
        """The lease is over (released, revoked and handed back, or timed
        out): take browsing back -- re-sync Songs (quietly: the spot check
        takes seconds, and the list stays "ready"), then play any pick that
        came in meanwhile."""
        nonlocal lease, pending_pick
        if not lease:
            return
        log.info("browse lease from %s ended (%s); re-syncing", lease["holder"], why)
        lease = None
        await publish_lease(bus)
        if phone is None or not phone.can_browse:
            # Gone, or back without browsing: what the crawl hid is true now.
            quiet["on"] = False
            await connect_current_phone(bus)
        else:
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
        # Managing phones (the jukebox's Phones menu and the admin).
        if action == "bluetooth":
            await set_bluetooth(bus, bool(cmd.get("on")))
            return
        if action == "pair_start":
            if not bt["on"]:
                log.info("pairing asked for with Bluetooth off; ignored")
                return
            await pairing.start()
            return
        if action == "pair_stop":
            await pairing.stop("cancelled at the jukebox")
            return
        if action == "pair_confirm":
            pairing.answered(bool(cmd.get("ok")))
            return
        if action == "switch" and cmd.get("mac"):
            asyncio.create_task(switch_to(bus, cmd["mac"]))
            return
        if action == "disconnect" and cmd.get("mac"):
            await disconnect_phone(bus, cmd["mac"])
            return
        if action == "forget" and cmd.get("mac"):
            await forget_phone(bus, cmd["mac"])
            return
        if phone is None and action == "play_item" and not lease:
            # Connected for audio only: the phone isn't letting us control it.
            log.warning("pick %r with no controls; reconnecting the phone", cmd.get("title"))
            asyncio.create_task(wait_for_phone(
                bus, {"cmd": cmd, "title": cmd.get("title"), "copy": 0, "stale": None}, "no_controls"))
            return
        if phone is None:
            log.warning("command %r received with no phone connected", action)
            return
        if action == "play_item" and lease:
            # A pick always wins over a deep crawl: revoke, and play once the
            # holder has handed browsing back (end_lease re-syncs first, so
            # the pick's stale path gets replayed by title).
            # Tell jb-core's pick doctor it's waiting (the hand-back and the
            # re-sync can take ~40s, past its 15s START_S), and the screen.
            pending_pick = cmd
            await pick_problem(bus, cmd, "busy", retrying=True)
            if not lease["revoked_at"]:
                lease["revoked_at"] = now()
                log.info("pick while %s holds the browse lease; revoking", lease["holder"])
                await publish_lease(bus)
            return
        if action == "stop":
            # "Done with this song": pause the phone, then report it as
            # stopped (see cleared_view).
            nonlocal cleared_title
            np = await phone.now_playing()
            await phone.transport("pause")
            cleared_title = np.get("title")
            return
        if action in ("play", "pause", "next", "previous"):
            await phone.transport(action)
        elif action == "play_item":
            if sync_lock.locked():
                # Reading the song list keeps BlueZ busy: the pick waits for it
                # (Phone.play_item), and the screen says so.
                await pick_problem(bus, cmd, "busy", retrying=True)
            try:
                await play_pick(cmd)
            except Exception as e:
                if isinstance(e, NotListed):
                    log.info("%r isn't where the song list says: reading every song again", cmd.get("title"))
                else:
                    log.exception("play_item failed: %r", cmd["item_path"])
                reason = "no_answer" if "timed out" in str(e).lower() or isinstance(e, asyncio.TimeoutError) else None
                if phone is None or not phone.wrong_app:
                    await pick_problem(bus, cmd, reason, retrying=True)
                if not await replay_after_resync(bus, cmd, reason or "no_answer", full=isinstance(e, NotListed)):
                    await bus.publish(f"jukebox/{SERVICE_NAME}/play-error", {**cmd, "reason": reason or "not_found"})
        elif action == "crawl":
            # Manual re-sync (e.g. from the phone manager UI, after adding
            # music on the phone): every song, whatever a spot check says.
            # Automatic syncs happen on connect.
            await sync_library(bus, full=True)
        else:
            log.warning("unknown command action: %r", action)

    def find_in_list(uid: str | None, title: str | None, copy: int = 0) -> int | None:
        """A song's place in the current list: by its ID, else by title and
        which copy of that title."""
        if uid and uid in by_uid:
            return by_uid[uid][0]
        places = [i for i, t in enumerate(last_tracks) if title and t["title"] == title]
        return places[min(copy, len(places) - 1)] if places else None

    async def play_at(index: int, again: bool = True):
        """Plays song `index` of the current list. After a spot check
        (songs.py) BlueZ has listed only a handful of songs: one it hasn't
        is listed on its own first (one round trip, never in the middle of
        a sync's listing) and must come back as the same song. Raises
        NotListed if it doesn't: the stored list is out of date. A sync
        that replaced the list meanwhile (a pick made during the re-sync
        after a reconnect or a crawl) is looked in again, once."""
        p, want = phone, last_tracks[index]
        if p is None:
            raise NotListed(want["item_path"])
        try:
            await p.play_item(want["item_path"])
            return
        except NotListed:
            pass
        listed = None
        async with sync_lock:
            replaced = not same_player(p) or index >= len(last_tracks) or last_tracks[index] is not want
            if not replaced:
                listed = await p.list_range(index, index)
        if replaced:
            moved = find_in_list(sg.uid_of(want["item_path"]), want["title"]) if again and phone else None
            if moved is None:
                raise NotListed(want["item_path"])
            await play_at(moved, again=False)
            return
        if not listed or sg.uid_of(listed[0]["item_path"]) != sg.uid_of(want["item_path"]) \
                or listed[0]["title"] != want["title"]:
            raise NotListed(want["item_path"])
        await p.play_item(listed[0]["item_path"])

    async def play_pick(cmd: dict):
        """Plays a pick: its song in this connection's list, found by its
        ID (a play path from before a reconnect names a player that's
        gone, but the ID at its end still holds), else the path as sent."""
        uid = sg.uid_of(cmd["item_path"])
        index = find_in_list(uid, None)
        if index is None and sync_running():
            # The list being read now may have it (a pick in the moment
            # between a connect and its sync taking the lock, too).
            await asyncio.wait({sync_task["task"]}, timeout=CRAWL_TIMEOUT_S)
            index = find_in_list(uid, None)
        elif index is None and sync_lock.locked():
            async with sync_lock:
                pass
            index = find_in_list(uid, None)
        if index is not None:
            await play_at(index)
        elif phone is None:
            raise NotListed(cmd["item_path"])
        else:
            await phone.play_item(cmd["item_path"])

    async def replay_after_resync(bus: Bus, cmd: dict, reason: str = "no_answer", full: bool = False) -> bool:
        """A pick failed, most likely because its play path belongs to a
        player BlueZ has since replaced (the phone switched apps and the
        re-sync hasn't happened or was missed). Re-sync now, find the same
        song in the fresh crawl, and play that -- the pick plays a few
        seconds late instead of being dropped. full: read every song (the
        song wasn't where the list said). Returns whether it played."""
        stale = cmd["item_path"]
        uid = sg.uid_of(stale)
        old = [t for t in last_tracks if uid and sg.uid_of(t["item_path"]) == uid]
        title = old[0]["title"] if old else cmd.get("title")
        if not title:
            return False
        # Which copy of the title (the library keeps same-title songs apart).
        copy = 0
        if old:
            copy = sum(1 for t in last_tracks[:last_tracks.index(old[0])] if t["title"] == title)
        pick = {"cmd": cmd, "title": title, "copy": copy, "stale": stale}
        if phone is not None and not phone.can_browse and not lease:
            # No browsing to re-sync with, and it may never come on its own:
            # reconnect for a fresh session, and play the pick after its sync.
            if phone.wrong_app:
                asyncio.create_task(wait_for_phone(bus, pick, "wrong_app"))
                return True
            log.info("play path looks stale and the phone isn't browsable; reconnecting to retry %r", title)
            asyncio.create_task(wait_for_phone(bus, pick, reason))
            return True
        log.info("play path looks stale; re-syncing to retry %r", title)
        try:
            await sync_library(bus, full=full)
        except BrowseLost as e:
            log.warning("re-sync for a stale play path: browsing is lost (%s)", e)
        except Exception:
            log.exception("re-sync for a stale play path failed")
        else:
            if phone is not None and phone.can_browse and phone.player_path == synced_player_path:
                return await play_fresh_copy(pick)
        # The re-sync didn't land (the phone stopped answering, or dropped its
        # controls mid-way, as on 2026-10-18): reconnect, and play it after.
        asyncio.create_task(wait_for_phone(bus, pick, reason))
        return True

    async def play_fresh_copy(pick: dict) -> bool:
        """Play the pick's song from the latest crawl, by title and copy."""
        title, copy = pick["title"], pick["copy"]
        index = find_in_list(None, title, copy)
        if index is None or phone is None:
            return False
        # The same path can be right: an iPhone's song paths survive a
        # reconnect, and the first try may have failed for another reason
        # (busy, no controls). Try it.
        try:
            await play_at(index)
        except Exception:
            log.exception("retry after re-sync failed: %r", title)
            return False
        log.info("played %r after re-sync", title)
        return True

    # -- which phone has the jukebox (phones.py has the policy) -------------
    lock = ph.Lock()
    # Phones disconnected on purpose (switched away from, disconnected or
    # forgotten from the menu): their going isn't a drop to reconnect after.
    leaving: dict[str, float] = {}
    turned_away_at: dict[str, float] = {}
    last_phone_list = None

    async def drop_link(mac: str, path: str):
        leaving[mac] = now() + 30
        intro = await system_bus.introspect(BLUEZ, path)
        dev = system_bus.get_proxy_object(BLUEZ, path, intro).get_interface("org.bluez.Device1")
        await asyncio.wait_for(dev.call_disconnect(), 10)

    async def pause_if_playing(mac: str):
        if phone is not None and phone.mac == mac and playing:
            try:
                await phone.transport("pause")
            except Exception:
                log.warning("couldn't pause %s before letting it go", phone.name)

    async def publish_switch(bus: Bus, state: str, **extra):
        await bus.publish(f"jukebox/{SERVICE_NAME}/switch", {"state": state, **extra}, retain=True)

    async def switch_to(bus: Bus, mac: str):
        """Hand the jukebox to another paired phone: pause and let go of the
        one it's with, then connect the new one."""
        paired = await rc.phones(system_bus)
        target = next((d for d in paired if d[0] == mac), None)
        if target is None:
            await publish_switch(bus, "failed", mac=mac, reason="not paired")
            return
        _, path, name, connected = target
        lock.switch_to(mac, now())
        await publish_switch(bus, "connecting", mac=mac, name=name)
        for other, other_path, other_name, other_connected in paired:
            if other_connected and other != mac:
                await pause_if_playing(other)
                try:
                    await drop_link(other, other_path)
                    log.info("switching: let go of %s", other_name)
                except Exception:
                    log.exception("switching: couldn't disconnect %s", other_name)
        if not connected:
            try:
                await rc.connect(system_bus, path)
            except Exception as e:
                log.info("switching to %s failed: %s", name, type(e).__name__)
                await publish_switch(bus, "failed", mac=mac, name=name,
                                     reason="Couldn't reach it. Is its Bluetooth on and nearby?")
                return
        log.info("switched to %s (%s)", name, mac)
        await publish_switch(bus, "done", mac=mac, name=name)

    async def disconnect_phone(bus: Bus, mac: str):
        paired = await rc.phones(system_bus)
        target = next((d for d in paired if d[0] == mac), None)
        if target is None or not target[3]:
            return
        await pause_if_playing(mac)
        if lock.owner == mac:
            lock.release()
        try:
            await drop_link(mac, target[1])
            log.info("disconnected %s on request", target[2])
        except Exception:
            log.exception("couldn't disconnect %s", target[2])

    async def let_go_for_app(bus: Bus):
        """The app has the jukebox now: disconnect the Bluetooth phone (often
        the same iPhone), so its audio can go to AirPlay. Not paused first:
        it's likely the app's own music on that phone."""
        for mac, path, name, connected in await rc.phones(system_bus):
            if not connected:
                continue
            if lock.owner == mac:
                lock.release()
            try:
                await drop_link(mac, path)
                log.info("the app has the jukebox: let go of %s on Bluetooth", name)
            except Exception:
                log.exception("couldn't let go of %s", name)

    async def forget_phone(bus: Bus, mac: str):
        """Unpair a phone and remove what the jukebox stored about it (the
        user's choice, 2026-10-14): jb-library deletes its music on
        jukebox/jb-source-phone/forgotten."""
        paired = await rc.phones(system_bus)
        target = next((d for d in paired if d[0] == mac), None)
        if target and target[3]:
            await pause_if_playing(mac)
        leaving[mac] = now() + 30
        lock.forget(mac)
        path = target[1] if target else f"{rc.ADAPTER}/dev_{mac.replace(':', '_')}"
        try:
            intro = await system_bus.introspect(BLUEZ, rc.ADAPTER)
            adapter = system_bus.get_proxy_object(BLUEZ, rc.ADAPTER, intro).get_interface("org.bluez.Adapter1")
            await asyncio.wait_for(adapter.call_remove_device(path), 15)
        except Exception:
            log.exception("couldn't remove %s from Bluetooth", mac)
        book.forget(mac)
        sg.forget(mac)
        if phone is not None and phone.mac == mac:
            await bus.clear_retained(f"jukebox/{SERVICE_NAME}/crawl-result")
        await bus.publish(f"jukebox/{SERVICE_NAME}/forgotten", {"mac": mac, "name": target[2] if target else None})
        log.info("forgot %s (%s)", target[2] if target else "phone", mac)

    bt = {"on": True}

    async def power_adapter(on: bool):
        intro = await system_bus.introspect(BLUEZ, rc.ADAPTER)
        props = system_bus.get_proxy_object(BLUEZ, rc.ADAPTER, intro).get_interface("org.freedesktop.DBus.Properties")
        await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", on))

    async def set_bluetooth(bus: Bus, on: bool, startup: bool = False):
        """Turn the jukebox's Bluetooth on or off. Off: pause and let go of
        the phone, stop reconnecting and pairing, power the radio down. On:
        power up and call the phones as at boot."""
        nonlocal reconnect_until
        if not startup and on == bt["on"]:
            return
        bt["on"] = on
        if not on:
            reconnect_until = 0.0
            if phone is not None:
                await pause_if_playing(phone.mac)
                leaving[phone.mac] = now() + 30
            lock.release()
            if pairing.is_open():
                await pairing.stop("Bluetooth turned off")
        try:
            await power_adapter(on)
        except Exception:
            log.exception("couldn't turn Bluetooth %s", "on" if on else "off")
        if not startup:
            remember_bluetooth(on)
            log.info("Bluetooth turned %s", "on" if on else "off")
        await bus.publish(f"jukebox/{SERVICE_NAME}/bluetooth", {"on": on}, retain=True)
        if on and not startup:
            start_reconnect(bus, "boot")

    async def on_paired(mac: str, name: str):
        """A new phone was paired at the jukebox: it gets the jukebox, once
        BlueZ lists it as a phone that can send audio."""
        book.touch(mac, name, time.time())
        for _ in range(15):
            if any(d[0] == mac for d in await rc.phones(system_bus)):
                break
            await asyncio.sleep(1)
        await switch_to(bus_ref["bus"], mac)

    # -- reconnecting on our own (reconnect.py has the policy) --------------
    book = rc.PhoneBook()
    bus_ref: dict = {}
    pairing = Pairing(system_bus, None, log)
    reconnect_task = None
    # Which kind of phone has the jukebox (jukebox/jb-core/source): "phone",
    # "helper" (the app), or None.
    chosen = {"kind": None, "seen": False}
    reconnect_until = 0.0

    async def publish_reconnect(bus: Bus, state: str, **extra):
        await bus.publish(f"jukebox/{SERVICE_NAME}/reconnect", {"state": state, **extra}, retain=True)

    def start_reconnect(bus: Bus, event: str, was_playing: bool = False):
        nonlocal reconnect_task, reconnect_until
        window = rc.window_for(event, was_playing, app_has_it=chosen["kind"] == "helper")
        if window is None:
            if chosen["kind"] == "helper":
                log.info("the app has the jukebox: not reconnecting Bluetooth (%s)", event)
            else:
                log.info("phone dropped while idle: not reconnecting (it may have been the owner's choice)")
            return
        reconnect_until = max(reconnect_until, now() + window)
        if reconnect_task is None or reconnect_task.done():
            reconnect_task = asyncio.create_task(reconnect_loop(bus, event))

    async def reconnect_loop(bus: Bus, event: str):
        started = now()
        log.info("reconnecting (%s), for up to %.0fs", event, reconnect_until - started)
        try:
            while now() < reconnect_until:
                if not bt["on"]:
                    return
                if chosen["kind"] == "helper":   # the source arrived after a boot reconnect began
                    log.info("the app has the jukebox: not reconnecting Bluetooth")
                    return
                if lease:
                    await publish_reconnect(bus, "paused", reason=f"{lease['holder']} holds the browse lease")
                elif not await rc.speaker_ready(system_bus):
                    await publish_reconnect(bus, "waiting", reason="audio not ready yet")
                else:
                    devs = await rc.phones(system_bus)
                    if any(d[3] for d in devs):
                        log.info("a phone is connected; reconnect done")
                        return
                    by_mac = {d[0]: d for d in devs}
                    order = book.order(list(by_mac))
                    if event in ("drop", "pick") and lock.owner in by_mac:
                        order = [lock.owner]   # its place is held; no one else
                    for mac in order:
                        _, path, name, _ = by_mac[mac]
                        await publish_reconnect(bus, "trying", name=name, mac=mac)
                        try:
                            await rc.connect(system_bus, path)
                            log.info("reconnected to %s (%s)", name, mac)
                            return
                        except Exception as e:
                            log.info("reconnect to %s failed: %s", name, type(e).__name__)
                await asyncio.sleep(rc.retry_delay(now() - started))
            log.info("reconnect gave up after %.0fs; tap Juk3box on the phone to connect", now() - started)
        finally:
            await publish_reconnect(bus, "idle")

    async def watch_connection(bus: Bus):
        """Starts reconnecting at boot and after a drop mid-song (option C),
        turns away phones that arrive while another has the jukebox
        (phones.py), and keeps the phone list up to date."""
        nonlocal last_phone_list
        was = None   # the phones connected at the last look
        last_playing_at = -1e9
        while True:
            try:
                if playing:
                    last_playing_at = now()
                paired = await rc.phones(system_bus)
                connected = [d[0] for d in paired if d[3]]
                info = {d[0]: d for d in paired}
                if was is None and not connected and bt["on"]:
                    start_reconnect(bus, "boot")
                elif was is not None and lock.owner in was and lock.owner not in connected:
                    mac = lock.owner
                    if leaving.get(mac, 0) < now():
                        mid_song = now() - last_playing_at < 10
                        lock.dropped(mac, now(), rc.DROP_WINDOW_S if mid_song else None)
                        start_reconnect(bus, "drop", was_playing=mid_song)
                for mac in lock.update(connected, now(), book.order(connected)):
                    if leaving.get(mac, 0) >= now():
                        continue   # already on its way out
                    name = info[mac][2]
                    log.info("turning away %s: %s has the jukebox", name, lock.owner)
                    try:
                        await drop_link(mac, info[mac][1])
                    except Exception:
                        log.exception("couldn't turn away %s", name)
                    if now() - turned_away_at.get(mac, -1e9) > 60:
                        turned_away_at[mac] = now()
                        owner = info.get(lock.owner)
                        await bus.publish("jukebox/jb-core/notice", {
                            "type": "phone_turned_away", "name": name, "owner": owner[2] if owner else None})
                if was is not None and set(connected) != was:
                    # Something came or went: what "connected" says follows
                    # (a device with no remote control raises no player events).
                    asyncio.create_task(connect_current_phone(bus))
                was = set(connected)
                listing = ph.phone_list(paired, book.phones, lock.owner)
                if listing != last_phone_list:
                    last_phone_list = listing
                    await bus.publish(f"jukebox/{SERVICE_NAME}/phones", {"phones": listing}, retain=True)
            except Exception:
                log.exception("connection watch failed")
            await asyncio.sleep(3)

    # A song cancelled with nothing after it (holding Reset): the phone can't
    # be told to forget it -- iOS ignores AVRCP Stop and just stays paused on
    # it -- so it's reported as stopped, nothing playing, until it plays
    # again or another song comes up.
    cleared_title = None

    def cleared_view(np: dict) -> dict:
        nonlocal cleared_title
        if cleared_title is None:
            return np
        if np.get("title") != cleared_title or np.get("status") == "playing":
            cleared_title = None
            return np
        return {**{k: None for k in np}, "status": "stopped"}

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
                    np = cleared_view(await phone.now_playing())
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
        await bus.subscribe("jukebox/jb-core/source")

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

        bus_ref["bus"] = bus
        # Bluetooth as the owner left it (on unless turned off).
        await set_bluetooth(bus, bluetooth_wanted(), startup=True)
        async def publish_pairing(state: dict):
            await bus.publish(f"jukebox/{SERVICE_NAME}/pairing", state, retain=True)
        pairing.publish = publish_pairing
        pairing.on_paired = on_paired
        pairing.claim = lambda mac: lock.switch_to(mac, now())
        try:
            await pairing.register()
        except Exception:
            log.exception("couldn't register the pairing agent; pairing from the jukebox won't work")
        await publish_switch(bus, "idle")

        asyncio.create_task(bus.run_heartbeat())
        asyncio.create_task(poll_now_playing(bus))
        asyncio.create_task(watch_lease(bus))
        asyncio.create_task(watch_connection(bus))
        async for topic, payload in messages:
            if topic == "jukebox/jb-core/source":
                before = chosen["kind"]
                chosen["kind"] = ((payload or {}).get("active") or {}).get("kind")
                first, chosen["seen"] = not chosen["seen"], True
                if chosen["kind"] == "helper" and reconnect_task and not reconnect_task.done():
                    log.info("the app has the jukebox: stopping the Bluetooth reconnect")
                    reconnect_task.cancel()
                if rc.app_took_over(before, chosen["kind"], first):
                    asyncio.create_task(let_go_for_app(bus))
                continue
            try:
                await handle_command(bus, payload)
            except Exception:
                log.exception("failed to handle command: %r", payload)


if __name__ == "__main__":
    asyncio.run(main())
