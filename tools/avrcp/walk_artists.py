import asyncio, sys, time, importlib.util
spec = importlib.util.spec_from_file_location("W", "/opt/juk3-box/services/jb_crawl/worker.py"); W = importlib.util.module_from_spec(spec); spec.loader.exec_module(W)
from dbus_next import BusType
from dbus_next.aio import MessageBus
W.emit = lambda o: print("  emit", o, flush=True) if "slow" in str(o) else None
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    w = W.Walker(bus, "88:51:F2:56:4B:2B")
    try:
        await w.new_session()
        await w.enter("My Music"); await w.enter("Artists")
        t = time.time(); listing = [(p, n) for p, n, f in await w.ls() if f]
        print("artists: %d listed+fetched in %.1fs" % (len(listing), time.time() - t), flush=True)
        t0 = time.time()
        for i, (path, name) in enumerate(listing[:N], 1):
            a = time.time(); await w.down(path, name); b = time.time(); items = await w.ls(); c = time.time(); await w.up(); d = time.time()
            print("%3d %-26s down %.1f ls %.1f (%d) up %.1f" % (i, name[:26], b-a, c-b, len(items), d-c), flush=True)
        print("avg %.2fs per artist" % ((time.time() - t0) / N), flush=True)
        await w.up(); await w.enter("Songs"); print("back in Songs", flush=True)
    except Exception as e:
        print("FAILED:", e, flush=True)
        await w.restore(); print("restored", w.stack[-1][1] if w.stack else "(new session)", flush=True)
asyncio.run(main())
