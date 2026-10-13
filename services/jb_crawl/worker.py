"""Deep-crawl worker: walks the phone's AVRCP folders and prints what it
finds, one JSON object per line on stdout. Run as a short-lived child of the
jb-crawl supervisor (one process per crawl, so nothing it allocates outlives
the crawl), only while the supervisor holds jb-source-phone's browse lease:

    python -m services.jb_crawl.worker --mac 88:51:F2:56:4B:2B --branches playlists,artists

Lines out:
    {"progress": "artists 120/303"}                 -- the supervisor's watchdog
    {"branch": "artists", "data": {...}}            -- one finished branch
    {"done": true} | {"error": "..."}

How BlueZ browsing has to be driven (learned the hard way, 2026-10-11):
  - the phone keeps ONE browse cursor; ChangeFolder moves it one level
  - the browse root can't be returned to (BlueZ treats the root object as
    the scope and refuses it), so each session starts with a reconnect,
    which always starts at the root with BlueZ's labels correct
  - go down only into folders just listed, and back up only to a parent
    we entered ourselves (never the root)
  - item names are BlueZ's path + "/" + the phone's name; names can contain
    "/" (AC/DC), so strip the known parent path instead of splitting
So: session 1 = reconnect, Playlists. Session 2 = reconnect, My Music's
branches one after another (Artists, Albums, Genres), ending in Songs so the
core sync starts from where it wants to be. SIGTERM (a pick revoked the
lease) stops at the next step and restores the cursor before exiting.
"""

import argparse
import asyncio
import json
import signal
import sys

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.errors import DBusError

BLUEZ = "org.bluez"
CALL_TIMEOUT_S = 15          # any single BlueZ call
CONNECT_TIMEOUT_S = 30
READY_TIMEOUT_S = 20         # browse root reporting items after a connect
STEP_PAUSE_S = 0.4           # between folder hops: the pace the working survey used
READ_RETRIES = 3             # a timed-out or "busy" read is tried again
RETRY_PAUSE_S = 2
BUSY_WAIT_S = 60             # how long to wait out BlueZ's background folder fetch
BUSY_POLL_S = 0.5
SETTLE_S = 2                 # after a fresh session reports ready, let the phone settle
COUNT_WAIT_S = 2             # how long a folder may report 0 items before we believe it
FETCH_WAIT_S = 60            # BlueZ fetching a whole big folder in the background
FETCH_POLL_S = 0.3
# Playlists folders that aren't the owner's playlists: "Recently Added"
# lists playlists (not songs), Playback History is the phone's own history
# (Recents stays jukebox-local, the user's call), Photos Memories is iOS's.
SKIP_PLAYLISTS = {"Recently Added", "Playback History", "Photos Memories"}
MY_MUSIC_BRANCHES = {"artists": "Artists", "albums": "Albums", "genres": "Genres"}  # crawl order


class Stop(Exception):
    """The supervisor asked us to stop (SIGTERM)."""


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


class Walker:
    def __init__(self, bus, mac: str):
        self.bus = bus
        self.mac = mac
        self.device = "/org/bluez/hci0/dev_" + mac.replace(":", "_")
        self.stop = asyncio.Event()
        self.folder = self.props = None
        self.root = None             # the player's /Filesystem object path
        self.stack = []              # [(object path, BlueZ name)] we entered, root first

    async def call(self, label: str, make, retries: int = 0):
        """One BlueZ call with a timeout; make() returns a fresh coroutine.

        "Busy" is waited out and retried for ANY call, folder changes too:
        BlueZ refuses a call before sending anything to the phone while it's
        still fetching the rest of a folder in the background -- it does
        that after every listing, in ~10-item chunks, however small the
        range asked for (seen in btmon, 2026-10-11: asked 0-59 of Artists,
        BlueZ kept fetching 40-302). Timeouts and other errors are retried
        only for reads (`retries`): a folder change that timed out may
        still have moved the phone's cursor."""
        attempt = 0
        busy_until = asyncio.get_running_loop().time() + BUSY_WAIT_S
        while True:
            if self.stop.is_set():
                raise Stop()
            try:
                return await asyncio.wait_for(make(), CALL_TIMEOUT_S)
            except DBusError as e:
                if "busy" in str(e).lower() and asyncio.get_running_loop().time() < busy_until:
                    await asyncio.sleep(BUSY_POLL_S)
                    continue
                err = e
            except asyncio.TimeoutError as e:
                err = e
            if attempt >= retries:
                raise RuntimeError(f"{label}: {type(err).__name__} {err}") from err
            attempt += 1
            await asyncio.sleep(RETRY_PAUSE_S)

    # -- session ---------------------------------------------------------
    async def new_session(self):
        """Reconnect the phone and wait until its browse root lists items."""
        dev_intro = await self.bus.introspect(BLUEZ, self.device)
        dev = self.bus.get_proxy_object(BLUEZ, self.device, dev_intro).get_interface("org.bluez.Device1")
        try:
            await asyncio.wait_for(dev.call_disconnect(), CALL_TIMEOUT_S)
        except Exception:
            pass  # already disconnected is fine
        await asyncio.sleep(3)
        # The audio profile, not a plain Connect: for an iPhone BlueZ can pick
        # Bluetooth LE and hang (jb_source_phone.reconnect.connect explains).
        await asyncio.wait_for(dev.call_connect_profile("0000110a-0000-1000-8000-00805f9b34fb"), CONNECT_TIMEOUT_S)
        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_S
        while True:
            if self.stop.is_set():
                raise Stop()
            player = await self.find_player()
            if player:
                obj = self.bus.get_proxy_object(BLUEZ, player, await self.bus.introspect(BLUEZ, player))
                try:
                    self.folder = obj.get_interface("org.bluez.MediaFolder1")
                    self.props = obj.get_interface("org.freedesktop.DBus.Properties")
                    name, count = await self.state()
                    if count > 0 and name == "/Filesystem":
                        await asyncio.sleep(SETTLE_S)
                        self.root = player + "/Filesystem"
                        self.stack = [(self.root, "/Filesystem")]
                        return
                except Exception:
                    pass  # interfaces still appearing
            if asyncio.get_running_loop().time() > deadline:
                raise RuntimeError("phone browsing didn't come up after reconnecting")
            await asyncio.sleep(0.5)

    async def find_player(self):
        om = self.bus.get_proxy_object(BLUEZ, "/", await self.bus.introspect(BLUEZ, "/")).get_interface(
            "org.freedesktop.DBus.ObjectManager")
        objs = await asyncio.wait_for(om.call_get_managed_objects(), CALL_TIMEOUT_S)
        for path, ifaces in objs.items():
            if str(path).startswith(self.device + "/player") and "org.bluez.MediaFolder1" in ifaces:
                return str(path)
        return None

    async def state(self) -> tuple[str, int]:
        a = await self.call("folder state", lambda: self.props.call_get_all("org.bluez.MediaFolder1"), READ_RETRIES)
        return a["Name"].value, a["NumberOfItems"].value

    # -- navigation ------------------------------------------------------
    async def ls(self) -> list:
        """[(object path, phone's own name, is_folder)] of the current folder."""
        here = self.stack[-1][1]
        # BlueZ updates the item count a moment after a folder change; read
        # too soon and a full folder looks empty (seen live: Artists listed
        # 0 items). A genuinely empty folder just costs the wait.
        for _ in range(int(COUNT_WAIT_S / 0.25)):
            _, count = await self.state()
            if count:
                break
            await asyncio.sleep(0.25)
        if not count:
            return []
        # One unbounded listing: whatever range is asked for, BlueZ goes on
        # fetching the whole folder from the phone in the background (~10
        # items per round trip), and anything sent meanwhile queues behind
        # it -- the next call then times out and its late reply derails
        # the step after (btmon, 2026-10-11). So wait until BlueZ holds
        # every item before moving on.
        out = []
        items = await self.call(f"list {here}", lambda: self.folder.call_list_items({}), READ_RETRIES)
        await self.wait_fetched(count)
        for path, props in items.items():
            is_folder = props["Type"].value == "folder" if "Type" in props else False
            if is_folder and str(path).rsplit("/", 1)[0] != self.stack[-1][0]:
                # BlueZ reuses an existing object when a listed item's phone
                # ID matches one it already has. For a folder that's a trap:
                # Artists' first entry comes back as the My Music folder
                # itself (seen live), and entering it would send the phone UP
                # and derail everything after, so it's skipped. Songs are
                # different: their IDs are global, so a song listed in an
                # album comes back as its object from Songs -- the very play
                # path the library already has. Those are kept.
                continue
            full = props["Name"].value if "Name" in props else ""
            name = full[len(here) + 1:] if full.startswith(here + "/") else full.rsplit("/", 1)[-1]
            out.append((str(path), name, is_folder))
        return out

    async def wait_fetched(self, count: int):
        """Until BlueZ has an object for every item of the current folder
        (its background fetch is done), or FETCH_WAIT_S."""
        folder_path = self.stack[-1][0]
        deadline = asyncio.get_running_loop().time() + FETCH_WAIT_S
        while asyncio.get_running_loop().time() < deadline:
            node = await asyncio.wait_for(self.bus.introspect(BLUEZ, folder_path), CALL_TIMEOUT_S)
            # one aliased entry (see ls) has no object here, hence the -1
            if len(node.nodes) >= count - 1:
                return
            await asyncio.sleep(FETCH_POLL_S)
        emit({"progress": f"slow fetch in {self.stack[-1][1]}"})

    async def down(self, path: str, name: str):
        await self.call(f"enter {name}", lambda: self.folder.call_change_folder(path))
        self.stack.append((path, f"{self.stack[-1][1]}/{name}"))
        await asyncio.sleep(STEP_PAUSE_S)

    async def up(self):
        """Back to the parent folder we came from (never the root itself)."""
        if len(self.stack) <= 2:
            raise RuntimeError("can't go up to the browse root; start a new session")
        self.stack.pop()
        await self.call(f"back to {self.stack[-1][1]}", lambda: self.folder.call_change_folder(self.stack[-1][0]))
        await asyncio.sleep(STEP_PAUSE_S)

    async def enter(self, name: str):
        for path, n, is_folder in await self.ls():
            if is_folder and n == name:
                await self.down(path, n)
                return
        raise RuntimeError(f"no folder {name!r} in {self.stack[-1][1]}")

    async def children(self, label: str, skip=()) -> dict:
        """{child folder name: [names of the items inside it]} for every
        child folder of the current folder, in the phone's order."""
        listing = [(p, n) for p, n, f in await self.ls() if f and n not in skip]
        emit({"progress": f"{label} 0/{len(listing)}"})
        result = {}
        for i, (path, name) in enumerate(listing, 1):
            await self.down(path, name)
            result[name] = [n for _, n, f in await self.ls() if not f]
            await self.up()
            emit({"progress": f"{label} {i}/{len(listing)}"})   # every step feeds the watchdog
        return result

    # -- branches --------------------------------------------------------
    async def playlists(self) -> dict:
        await self.enter("Playlists")
        return {"playlists": await self.children("playlists", skip=SKIP_PLAYLISTS)}

    async def my_music_branch(self, branch: str) -> dict:
        await self.enter(MY_MUSIC_BRANCHES[branch])
        if branch == "albums":
            # "Recently Added" lists album names newest first: the true order
            # for the New row. Read it, then every real album's songs.
            recent = []
            for path, name, is_folder in await self.ls():
                if is_folder and name == "Recently Added":
                    await self.down(path, name)
                    recent = [n for _, n, f in await self.ls() if f]
                    await self.up()
                    break
            data = {"albums": await self.children("albums", skip={"Recently Added"}), "recent": recent}
        else:
            data = {"artists": await self.children("artists")}
        await self.up()
        return data

    async def genres(self) -> dict:
        """Genres hold artist folders, not songs: {genre: [artist names]}."""
        await self.enter("Genres")
        listing = [(p, n) for p, n, f in await self.ls() if f and n != "Recently Added"]
        emit({"progress": f"genres 0/{len(listing)}"})
        out = {}
        for i, (path, name) in enumerate(listing, 1):
            await self.down(path, name)
            out[name] = [n for _, n, f in await self.ls() if f and n != "Recently Added"]
            await self.up()
            emit({"progress": f"genres {i}/{len(listing)}"})
        await self.up()
        return {"genres": out}

    # -- albums, starting from where the library sync leaves the cursor --
    # Opening Albums from Songs needs no reconnect: up one level to My Music,
    # then down. Measured 2026-10-12: album list ~24 s (BlueZ fetching 430
    # names), one album's songs <1 s, back to Songs ~21 s.

    async def known_folders(self) -> dict:
        """{BlueZ folder name: object path} for the folders BlueZ knows."""
        om = self.bus.get_proxy_object(BLUEZ, "/", await self.bus.introspect(BLUEZ, "/")).get_interface(
            "org.freedesktop.DBus.ObjectManager")
        objs = await asyncio.wait_for(om.call_get_managed_objects(), CALL_TIMEOUT_S)
        out = {}
        for path, ifaces in objs.items():
            it = ifaces.get("org.bluez.MediaItem1")
            if it and "Type" in it and it["Type"].value == "folder" and "Name" in it:
                out[it["Name"].value] = str(path)
        return out

    async def to_my_music(self):
        """Into My Music: one step up from Songs when that's where the cursor
        is (the normal case), else a fresh session from the root."""
        player = await self.find_player()
        if player:
            obj = self.bus.get_proxy_object(BLUEZ, player, await self.bus.introspect(BLUEZ, player))
            self.folder = obj.get_interface("org.bluez.MediaFolder1")
            self.props = obj.get_interface("org.freedesktop.DBus.Properties")
            name, _ = await self.state()
            known = await self.known_folders()
            if name == "/Filesystem/My Music/Songs" and "/Filesystem/My Music" in known:
                self.root = player + "/Filesystem"
                self.stack = [(self.root, "/Filesystem"), (known["/Filesystem/My Music"], "/Filesystem/My Music"),
                              (known[name], name)]
                await self.up()
                return
        await self.new_session()
        await self.enter("My Music")

    async def child(self, name: str):
        """Down into a child of the current folder, by BlueZ's known object
        when it has one (skips re-listing the parent), else by listing."""
        full = f"{self.stack[-1][1]}/{name}"
        path = (await self.known_folders()).get(full)
        if path and path.rsplit("/", 1)[0] == self.stack[-1][0]:
            await self.down(path, name)
        else:
            await self.enter(name)

    async def album_names(self) -> list:
        """Inside Albums: every album's name, in the phone's order."""
        return [n for _, n, f in await self.ls() if f and n != "Recently Added"]

    async def album_index(self) -> dict:
        """From My Music: the list of albums and the phone's newest-first
        order (Albums > Recently Added). Names only, nothing opened."""
        await self.child("Albums")
        listing = await self.ls()
        names = [n for _, n, f in listing if f and n != "Recently Added"]
        emit({"progress": f"album list {len(names)}"})
        recent = []
        ra = next((p for p, n, f in listing if f and n == "Recently Added"), None)
        if ra:
            await self.down(ra, "Recently Added")
            recent = [n for _, n, f in await self.ls() if f]
            await self.up()
        await self.up()
        return {"albums": names, "recent": recent}

    async def album(self, name: str, nth: int = 0, key: str | None = None) -> dict:
        """From My Music: one album's songs, in album order, with each song's
        phone ID -- the same ID as in Songs, so it maps exactly onto the
        library's play path (seen 2026-10-12). nth picks which album of that
        name (they can repeat); the result is stored under `key`."""
        await self.child("Albums")
        listing = await self.ls()
        emit({"progress": f"albums listed ({len(listing)})"})
        matches = [p for p, n, f in listing if f and n == name]
        path = matches[nth] if nth < len(matches) else None
        tracks = None
        if path:
            await self.down(path, name)
            tracks = [{"title": n, "uid": p.rsplit("/item", 1)[-1]} for p, n, f in await self.ls() if not f]
            await self.up()
        await self.up()
        return {"name": key or name, "tracks": tracks}

    async def back_to_songs(self):
        while len(self.stack) > 2:
            await self.up()
        await self.child("Songs")

    async def restore(self):
        """Leave the cursor where the core sync can find Songs: if we're
        inside My Music, walk up to it and into Songs; otherwise reconnect
        (a fresh session starts at the root, which the core sync handles)."""
        self.stop.clear()
        try:
            names = [n for _, n in self.stack]
            if len(names) >= 2 and names[1] == "/Filesystem/My Music":
                while len(self.stack) > 2:
                    await self.up()
                await self.enter("Songs")
                return
        except Exception:
            pass
        await self.new_session()


async def run(mac: str, branches: list, album: str | None = None, nth: int = 0, key: str | None = None):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    w = Walker(bus, mac)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, w.stop.set)
    try:
        if album is not None:
            # On demand (someone opened an album): no reconnect, back to Songs after.
            await w.to_my_music()
            emit({"album": await w.album(album, nth, key)})
            await w.back_to_songs()
            emit({"done": True})
            return
        if "album_index" in branches:
            await w.to_my_music()
            emit({"branch": "album_index", "data": await w.album_index()})
            await w.back_to_songs()
        if "playlists" in branches:
            await w.new_session()
            emit({"branch": "playlists", "data": await w.playlists()})
        mine = [b for b in branches if b in MY_MUSIC_BRANCHES]
        if mine:
            await w.new_session()
            await w.enter("My Music")
            for b in mine:
                data = await (w.genres() if b == "genres" else w.my_music_branch(b))
                emit({"branch": b, "data": data})
            await w.enter("Songs")
        elif w.stack:
            await w.restore()
        emit({"done": True})
    except Stop:
        emit({"stopped": True})
        await w.restore()
    except Exception as e:
        emit({"error": f"{type(e).__name__}: {e}"})
        try:
            await w.restore()
        except Exception as e2:
            emit({"error": f"restore failed: {type(e2).__name__}: {e2}"})
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", required=True)
    ap.add_argument("--branches", default="", help="comma list: album_index,playlists,artists,albums,genres")
    ap.add_argument("--album", help="fetch just this album's songs (on demand): its title")
    ap.add_argument("--nth", type=int, default=0, help="which album of that title (0 = first)")
    ap.add_argument("--key", help="the album's key to store it under (default: the title)")
    args = ap.parse_args()
    asyncio.run(run(args.mac, [b for b in args.branches.split(",") if b], args.album, args.nth, args.key))


if __name__ == "__main__":
    main()
