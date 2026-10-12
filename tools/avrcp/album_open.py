"""Times opening one album the way a car would, starting from where the
library sync leaves the phone (Songs) -- no reconnect, nothing played:
Songs -> up to My Music -> Albums (wait for all of it) -> one album (list
its songs) -> back up -> Songs. Run through with_lease.sh:

    tools/avrcp/with_lease.sh tools/avrcp/album_open.py "Dawn FM"
"""
import asyncio
import sys
import time

from dbus_next import BusType
from dbus_next.aio import MessageBus

sys.path.insert(0, "/opt/juk3-box")
from services.jb_crawl import worker as W  # noqa: E402

W.emit = lambda o: print("  ", o, flush=True)
MAC = "88:51:F2:56:4B:2B"
ALBUM = sys.argv[1] if len(sys.argv) > 1 else "Dawn FM"


async def folder_objects(bus):
    om = bus.get_proxy_object(W.BLUEZ, "/", await bus.introspect(W.BLUEZ, "/")).get_interface(
        "org.freedesktop.DBus.ObjectManager")
    out = {}
    for path, ifaces in (await om.call_get_managed_objects()).items():
        it = ifaces.get("org.bluez.MediaItem1")
        if it and it.get("Type") and it["Type"].value == "folder":
            out[it["Name"].value] = str(path)
    return out


async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    w = W.Walker(bus, MAC)
    player = await w.find_player()
    obj = bus.get_proxy_object(W.BLUEZ, player, await bus.introspect(W.BLUEZ, player))
    w.folder = obj.get_interface("org.bluez.MediaFolder1")
    w.props = obj.get_interface("org.freedesktop.DBus.Properties")
    name, count = await w.state()
    print(f"start: {name} ({count})")
    f = await folder_objects(bus)
    mm, songs = f["/Filesystem/My Music"], f["/Filesystem/My Music/Songs"]
    w.root = player + "/Filesystem"
    w.stack = [(w.root, "/Filesystem"), (mm, "/Filesystem/My Music"), (songs, "/Filesystem/My Music/Songs")]
    steps = []

    async def step(label, coro):
        t = time.time()
        r = await coro
        steps.append((label, time.time() - t))
        print(f"{label:<34} {time.time() - t:5.1f}s", flush=True)
        return r

    try:
        await step("up to My Music", w.up())
        await step("enter Albums", w.enter("Albums"))
        albums = await step("list + fetch all albums", w.ls())
        print(f"   {len(albums)} albums")
        match = [(p, n) for p, n, is_f in albums if is_f and n == ALBUM]
        if not match:
            print(f"   no album {ALBUM!r}; first few: {[n for _, n, _ in albums[:8]]}")
        else:
            await step(f"open {ALBUM!r}", w.down(*match[0]))
            await asyncio.sleep(3)
            print("   state inside:", await w.state())
            raw = await w.folder.call_list_items({})
            for p, i in list(raw.items())[:20]:
                print("   raw", str(p).split("player0")[1], "|", i["Name"].value, "|", i["Type"].value)
            tracks = await step("list its songs", w.ls())
            for i, (p, n, is_f) in enumerate(tracks, 1):
                print(f"   {i:2}. {n}   ({p.rsplit('/', 1)[-1]})")
            await step("back up to Albums", w.up())
        await step("back up to My Music", w.up())
        await step("enter Songs", w.enter("Songs"))
        print(f"TOTAL {sum(s for _, s in steps):.1f}s")
    except Exception as e:
        print("FAILED:", e)
        await w.restore()
        print("restored")


asyncio.run(main())
