"""jb-crawl: the deep crawl of the phone's library (playlists, real artists,
albums and genres), run apart from the core so nothing it does can break
picking songs.

This process is the supervisor; it stays small and never touches BlueZ.
Each crawl runs in a short-lived child process (services.jb_crawl.worker)
that exits when done, so nothing a crawl allocates outlives it, and the
systemd unit caps memory and CPU on top of that. The supervisor:
  - meters crawls (policy.py): due on a phone's first connect of the day,
    resumed until every branch is stored, only while the jukebox is idle
  - takes turns with jb-source-phone through its browse lease: asks before
    crawling, hands it back after; a pick revokes it, and the worker is
    told to stop and put the browse cursor back (SIGTERM), then killed if
    it doesn't exit in time
  - watchdogs the worker: no output for PROGRESS_TIMEOUT_S, or a crawl
    longer than CRAWL_MAX_S, kills it
  - hands each finished branch to jb-library and only counts it done once
    jb-library confirms it stored it
  - backs off after a failed crawl, and gives up for the day after a few

Topics:
  in : jukebox/jb-source-phone/connected, /now-playing, /lease
       jukebox/jb-input/key, /gesture  (activity, for idleness)
       jukebox/jb-library/deep-ack     {mac, branch, cycle, ok}
       jukebox/jb-crawl/cmd            {"action": "crawl_now"} skips the idle wait
  out: jukebox/jb-crawl/result         {mac, branch, cycle, data}
       jukebox/jb-crawl/status         (retained) what it's doing and why
"""

import asyncio
import json
import signal
import sys
import time
import uuid

from shared import config, logging as jb_logging
from shared.bus import Bus
from services.jb_crawl import policy

SERVICE_NAME = "jb-crawl"
HOLDER = SERVICE_NAME
TICK_S = 15
IDLE_S = 300                 # no keypad/knob use and nothing playing for this long
SETTLE_S = 120               # phone connected at least this long (core sync done)
LEASE_TTL_S = 1200
LEASE_WAIT_S = 10
PROGRESS_TIMEOUT_S = 45      # worker silent this long = stuck
CRAWL_MAX_S = 1100           # stays inside the lease
STOP_GRACE_S = 25            # SIGTERM -> restore the cursor -> exit
ACK_TIMEOUT_S = 20
STATUS_EVERY_S = 5
RETRY_AFTER_FAIL_S = 1800
MAX_FAILURES_PER_DAY = 3
# Without /etc/juk3-box/jb-crawl.json: the cheap branches only. The full
# artist/album/genre walks take hours over Bluetooth.
DEFAULT_BRANCHES = ("album_index", "playlists")


class Supervisor:
    def __init__(self, bus: Bus, cfg: dict, log):
        self.bus, self.log = bus, log
        self.enabled = tuple(b for b in cfg.get("branches", DEFAULT_BRANCHES) if b in policy.BRANCHES)
        self.album_queue = []        # album names someone opened, oldest first
        self.wake = asyncio.Event()  # an album request shouldn't wait for the next tick
        self.state = policy.load()
        self.mac = None
        self.connected_since = None
        self.playing = False
        self.last_activity = None
        self.lease = {}
        self.acks = {}               # (mac, branch, cycle) -> asyncio.Future
        self.worker = None           # asyncio.subprocess.Process
        self.retry_at = 0.0
        self.failures = {}           # day -> count
        self.force = False
        self.status = {}

    async def set_status(self, **s):
        s.setdefault("mac", self.mac)
        s["pending"] = policy.pending(self.state, self.mac, self.enabled) if self.mac else []
        if self.mac:
            s["last_complete"] = policy.phone(self.state, self.mac).get("last_complete")
        if s != self.status:
            self.status = s
            await self.bus.publish(f"jukebox/{SERVICE_NAME}/status", s, retain=True)

    # -- inputs ------------------------------------------------------------
    async def on_message(self, topic: str, p: dict):
        if topic.endswith("/connected"):
            if self.worker:
                return  # the worker's own reconnects aren't the owner connecting
            if p.get("connected"):
                if p.get("mac") != self.mac or self.connected_since is None:
                    self.mac, self.connected_since = p.get("mac"), time.time()
                    policy.settle(self.state, self.mac, time.time(), self.enabled)
                    if policy.on_connect(self.state, self.mac, time.time()):
                        self.log.info("first connect today for %s: deep crawl due", self.mac)
                    policy.save(self.state)
            else:
                self.connected_since = None
        elif topic.endswith("/now-playing"):
            self.playing = p.get("status") == "playing"
        elif topic.endswith("/lease"):
            self.lease = p
            if self.worker and p.get("revoked") and p.get("holder") == HOLDER:
                self.log.info("browse lease revoked (a pick): stopping the crawl")
                self.stop_worker()
        elif topic.startswith("jukebox/jb-input/"):
            self.last_activity = time.time()
        elif topic.endswith("/deep-ack"):
            fut = self.acks.get((p.get("mac"), p.get("branch"), p.get("cycle")))
            if fut and not fut.done():
                fut.set_result(bool(p.get("ok")))
        elif topic.endswith(f"{SERVICE_NAME}/cmd") and p.get("action") == "crawl_now":
            self.force = True
            self.wake.set()
        elif topic.endswith(f"{SERVICE_NAME}/cmd") and p.get("action") == "album" and p.get("name"):
            # Someone opened an album whose songs aren't known yet: fetch
            # just that one, now (no idle wait -- a person is waiting).
            if p["name"] not in self.album_queue:
                self.album_queue.append(p["name"])
                await self.album_state(p["name"], "loading")
            self.wake.set()

    async def album_state(self, name: str, state: str, reason: str | None = None):
        """jukebox/jb-crawl/album (retained): the latest album request's state,
        so the board can show "Loading album..." or why it failed."""
        await self.bus.publish(f"jukebox/{SERVICE_NAME}/album",
                               {"name": name, "state": state, "reason": reason, "at": time.time()}, retain=True)

    # -- the loop ----------------------------------------------------------
    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                self.log.exception("crawl tick failed")
            try:
                await asyncio.wait_for(self.wake.wait(), TICK_S)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def fetch_albums(self):
        """On-demand album fetches, one worker per album, before any metered
        crawl. A pick still revokes the lease and stops one mid-way."""
        while self.album_queue and self.mac and not self.worker:
            name = self.album_queue[0]
            if not await self.take_lease():
                await self.album_state(name, "waiting", f"phone busy: {self.lease.get('reason')}")
                return  # try again on the next tick
            ok = False
            try:
                ok = await self.crawl(["--album", name], label=f"album {name!r}")
            finally:
                await self.bus.publish("jukebox/jb-source-phone/cmd", {"action": "lease_release", "holder": HOLDER})
            self.album_queue.pop(0)
            if not ok:
                await self.album_state(name, "failed", "the phone didn't answer; try again")

    async def tick(self):
        if self.worker or not self.mac:
            return
        if self.album_queue:
            await self.fetch_albums()
            return
        pending = policy.pending(self.state, self.mac, self.enabled)
        if not pending and not self.force:
            await self.set_status(state="idle", reason="nothing due")
            return
        if not pending:  # crawl_now with nothing due: crawl everything again
            pending = list(self.enabled)
        today = policy.day(time.time())
        if self.failures.get(today, 0) >= MAX_FAILURES_PER_DAY:
            await self.set_status(state="waiting", reason="too many failures today")
            return
        if time.time() < self.retry_at and not self.force:
            await self.set_status(state="waiting", reason="backing off after a failure")
            return
        idle, why = policy.is_idle(time.time(), self.last_activity, self.playing,
                                   self.connected_since, IDLE_S, SETTLE_S)
        if not idle and not self.force:
            await self.set_status(state="waiting", reason=why)
            return
        self.force = False
        if not await self.take_lease():
            self.retry_at = time.time() + 300
            await self.set_status(state="waiting", reason=f"lease denied: {self.lease.get('reason')}")
            return
        ok = False
        try:
            ok = await self.crawl(["--branches", ",".join(pending)], label=",".join(pending), expect=set(pending))
        finally:
            await self.bus.publish("jukebox/jb-source-phone/cmd", {"action": "lease_release", "holder": HOLDER})
        if not ok:
            self.failures[today] = self.failures.get(today, 0) + 1
            self.retry_at = time.time() + RETRY_AFTER_FAIL_S
        await self.set_status(state="idle" if ok else "waiting",
                              reason="crawl finished" if ok else "crawl incomplete; will resume")

    async def take_lease(self) -> bool:
        await self.bus.publish("jukebox/jb-source-phone/cmd",
                               {"action": "lease_request", "holder": HOLDER, "ttl_s": LEASE_TTL_S})
        for _ in range(int(LEASE_WAIT_S / 0.5)):
            await asyncio.sleep(0.5)
            if self.lease.get("holder") == HOLDER:
                return True
            if self.lease.get("denied") == HOLDER:
                return False
        return False

    async def crawl(self, args: list, label: str, expect=frozenset({"album"})) -> bool:
        """Runs one worker (args: --branches ... or --album NAME) and hands
        what it finds to jb-library. True when it finished and everything
        in `expect` was stored."""
        cycle = uuid.uuid4().hex[:8]
        self.log.info("deep crawl %s of %s: %s", cycle, self.mac, label)
        await self.set_status(state="crawling", reason=label)
        self.worker = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "services.jb_crawl.worker", "--mac", self.mac, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=8 * 1024 * 1024,
        )
        started = last_status = time.time()
        stored, finished = set(), False
        try:
            while True:
                remaining = CRAWL_MAX_S - (time.time() - started)
                if remaining <= 0:
                    self.log.error("deep crawl over %ds; stopping it", CRAWL_MAX_S)
                    self.stop_worker()
                    break
                try:
                    line = await asyncio.wait_for(self.worker.stdout.readline(), min(PROGRESS_TIMEOUT_S, remaining))
                except asyncio.TimeoutError:
                    self.log.error("deep crawl worker silent for %ds; stopping it", PROGRESS_TIMEOUT_S)
                    self.stop_worker()
                    break
                if not line:
                    break  # worker exited
                msg = self.parse(line)
                if "progress" in msg:
                    # Every line resets the watchdog; the public status only
                    # needs refreshing every few seconds.
                    if time.time() - last_status >= STATUS_EVERY_S:
                        last_status = time.time()
                        await self.set_status(state="crawling", reason=msg["progress"])
                elif "branch" in msg:
                    if await self.store(cycle, msg["branch"], msg["data"]):
                        stored.add(msg["branch"])
                elif "album" in msg:
                    album = msg["album"]
                    if album.get("tracks") is None:
                        await self.album_state(album["name"], "failed", "the phone has no album by that name")
                        stored.add("album")  # a real answer, not a failure to retry
                    elif await self.store(cycle, "album", album):
                        stored.add("album")
                        await self.album_state(album["name"], "done")
                elif msg.get("done"):
                    finished = True
                elif "error" in msg or msg.get("stopped"):
                    self.log.warning("deep crawl worker: %s", msg)
        finally:
            await self.reap()
        self.log.info("deep crawl %s: stored %s%s", cycle, sorted(stored) or "nothing",
                      "" if finished else " (incomplete)")
        return finished and stored >= set(expect)

    def parse(self, line: bytes) -> dict:
        try:
            return json.loads(line)
        except ValueError:
            return {}

    async def store(self, cycle: str, branch: str, data: dict) -> bool:
        key = (self.mac, branch, cycle)
        self.acks[key] = asyncio.get_running_loop().create_future()
        await self.bus.publish(f"jukebox/{SERVICE_NAME}/result",
                               {"mac": self.mac, "branch": branch, "cycle": cycle, "data": data})
        try:
            ok = await asyncio.wait_for(self.acks[key], ACK_TIMEOUT_S)
        except asyncio.TimeoutError:
            ok = False
            self.log.error("jb-library didn't confirm the %s branch; it will be crawled again", branch)
        finally:
            self.acks.pop(key, None)
        if ok and branch in policy.BRANCHES:
            if policy.branch_done(self.state, self.mac, branch, time.time(), self.enabled):
                self.log.info("deep crawl cycle complete for %s", self.mac)
            policy.save(self.state)
        return ok

    def stop_worker(self):
        if self.worker and self.worker.returncode is None:
            self.worker.send_signal(signal.SIGTERM)

    async def reap(self):
        """Wait for the worker to exit (it restores the browse cursor on
        SIGTERM); kill it if it takes too long. Never leaves a child behind."""
        w, self.worker = self.worker, None
        if w is None:
            return
        try:
            await asyncio.wait_for(w.wait(), STOP_GRACE_S)
        except asyncio.TimeoutError:
            self.log.error("deep crawl worker didn't exit; killing it")
            w.kill()
            await w.wait()
        err = (await w.stderr.read()).decode(errors="replace").strip()
        if err:
            self.log.warning("worker stderr: %s", err[-2000:])


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        sup = Supervisor(bus, cfg, log)
        messages = await bus.subscribe("jukebox/jb-source-phone/connected")
        for t in ("jukebox/jb-source-phone/now-playing", "jukebox/jb-source-phone/lease",
                  "jukebox/jb-input/key", "jukebox/jb-input/gesture",
                  "jukebox/jb-library/deep-ack", f"jukebox/{SERVICE_NAME}/cmd"):
            await bus.subscribe(t)
        # A crash or restart mid-crawl kills the worker with us (same cgroup)
        # but would leave our lease held until it times out: hand it back.
        await bus.publish("jukebox/jb-source-phone/cmd", {"action": "lease_release", "holder": HOLDER})
        asyncio.create_task(bus.run_heartbeat())
        loop_task = asyncio.create_task(sup.run())
        # Like jb-input: a dead loop task must take the service down so
        # systemd restarts it, not leave a zombie that never crawls.
        loop_task.add_done_callback(lambda t: sys.exit(1) if not t.cancelled() else None)
        async for topic, payload in messages:
            try:
                await sup.on_message(topic, payload)
            except Exception:
                log.exception("bad message on %s", topic)


if __name__ == "__main__":
    asyncio.run(main())
