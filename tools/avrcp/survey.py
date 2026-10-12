"""AVRCP browse survey: one fresh connection per branch (a new session starts
at the root, the only state where BlueZ's folder bookkeeping is right).
Read-only: browses, never plays. Run with jb-source-phone stopped."""
import asyncio, json, subprocess, sys
from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant
from dbus_next.errors import DBusError

MAC = sys.argv[1]
DEV = "/org/bluez/hci0/dev_" + MAC.replace(":", "_")


def val(x):
    x = getattr(x, "value", x)
    if isinstance(x, dict):
        return {k: val(y) for k, y in x.items()}
    if isinstance(x, list):
        return [val(y) for y in x]
    return x


def bt(*args):
    return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=40).stdout


async def fresh_player(bus):
    bt("disconnect", MAC)
    await asyncio.sleep(4)
    bt("connect", MAC)
    for _ in range(40):
        await asyncio.sleep(0.5)
        om = bus.get_proxy_object("org.bluez", "/", await bus.introspect("org.bluez", "/")).get_interface(
            "org.freedesktop.DBus.ObjectManager")
        objs = await om.call_get_managed_objects()
        for p, i in objs.items():
            if str(p).startswith(DEV + "/player") and "org.bluez.MediaFolder1" in i:
                p = str(p)
                o = bus.get_proxy_object("org.bluez", p, await bus.introspect("org.bluez", p))
                return o.get_interface("org.bluez.MediaFolder1"), o.get_interface("org.freedesktop.DBus.Properties")
    raise RuntimeError("no browsable player after reconnect")


class Nav:
    def __init__(self, folder, props):
        self.f, self.p = folder, props

    async def info(self):
        a = await self.p.call_get_all("org.bluez.MediaFolder1")
        return a["Name"].value, a["NumberOfItems"].value

    async def ls(self, n=None):
        _, total = await self.info()
        n = total if n is None else min(n, total)
        if n == 0:
            return []
        items = await self.f.call_list_items({"Start": Variant("u", 0), "End": Variant("u", n - 1)})
        return [(str(p), val(i)) for p, i in items.items()]

    async def cd(self, path):
        await self.f.call_change_folder(path)
        await asyncio.sleep(0.4)


def short(name):
    return name.rsplit("/", 1)[-1]


def describe(items, k=8):
    folders = [short(i["Name"]) for _, i in items if i.get("Type") == "folder"]
    tracks = [i for _, i in items if i.get("Type") != "folder"]
    return {"folders": folders[:40], "n_folders": len(folders), "n_tracks": len(tracks),
            "track_samples": [{"Name": short(t.get("Name", "")), "Metadata": t.get("Metadata")} for t in tracks[:k]]}


async def walk(nav, path_names, peek_children=3, peek_items=8):
    """From the root, go down by name; describe the end folder and peek
    into its first few child folders (down, then back up one level)."""
    for name in path_names:
        items = await nav.ls()
        target = next((p for p, i in items if i.get("Type") == "folder" and short(i["Name"]) == name), None)
        if not target:
            return {"missing": name, "have": [short(i["Name"]) for _, i in items]}
        await nav.cd(target)
        here_obj = target
    here, total = await nav.info()
    items = await nav.ls(60)
    node = {"at": here, "count": total, **describe(items, peek_items), "children": {}}
    for p, i in [(p, i) for p, i in items if i.get("Type") == "folder"][:peek_children]:
        try:
            await nav.cd(p)
            n2, c2 = await nav.info()
            node["children"][short(i["Name"])] = {"count": c2, **describe(await nav.ls(12), 5)}
            await nav.cd(here_obj)
        except DBusError as e:
            node["children"][short(i["Name"])] = {"error": str(e)}
            break
    return node


async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    out = {}
    branches = [["Playlists"], ["My Music", "Artists"], ["My Music", "Albums"],
                ["My Music", "Genres"], ["Radio"]]
    for b in branches:
        f, p = await fresh_player(bus)
        nav = Nav(f, p)
        if not out.get("root"):
            out["root"] = describe(await nav.ls())
        try:
            out["/".join(b)] = await walk(nav, b)
        except DBusError as e:
            out["/".join(b)] = {"error": str(e)}
    # Search, from a fresh root.
    f, p = await fresh_player(bus)
    try:
        r = await f.call_search("love", {})
        nav = Nav(f, p)
        out["search"] = {"returned": str(r), "at": await nav.info(), **describe(await nav.ls(10), 10)}
    except DBusError as e:
        out["search"] = {"error": str(e)}
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))


asyncio.run(main())
