import asyncio
import random
import time

from shared import config, logging as jb_logging
from shared.bus import Bus
from shared.jb_entry.entry import Entry, EntryChanged, Rejected, Selection
from services.jb_core.end_guard import EndGuard
from services.jb_core.power import MENU_TIMEOUT_S, PowerMenu

SERVICE_NAME = "jb-core"
ENTRY_TIMEOUT_MS = 10000   # a waiting letter clears after this long with no digit
ENTRY_TICK_S = 0.25

SHUFFLE_BUFFER_TARGET = 20


class Queue:
    """Owns what plays next. iOS's own shuffle/queue is never used -- every
    transition goes through our own PlayItem, always. Two tiers, checked in
    order: manual selections always drain first (so a run of manual picks
    plays back in the order they were entered), then shuffle picks (which
    keep refilling indefinitely while shuffle is active). A manual pick
    during shuffle therefore jumps ahead of it for free, without needing any
    special-case insert logic -- the shuffle tail is just still sitting
    there underneath, resuming once the manual tier empties again.
    """

    def __init__(self):
        self.manual: list[dict] = []
        self.shuffle_buffer: list[dict] = []
        self.shuffle_active = False
        self.library_tracks: list[dict] = []
        self.expected_title: str | None = None
        self.last_seen_title: str | None = None
        # Whether the phone is currently playing (from now-playing status),
        # so a selection made while idle starts right away instead of
        # waiting in the queue. Set optimistically when we command a play so
        # a second quick selection queues behind it instead of replacing it.
        self.playing = False
        # Readiness check before playing anything -- what this means
        # depends on the active source. Only the phone source exists so
        # far; Library/Spotify (Phases 9/10+, not built) will need their
        # own equivalent ("are there any songs loaded locally?", etc.)
        # when they exist.
        self.phone_connected = False
        # The power menu (holding Reset) holds the queue: nothing starts on
        # its own until a choice is made.
        self.held = False

    def refill_shuffle(self) -> None:
        if not self.shuffle_active or not self.library_tracks:
            return
        while len(self.shuffle_buffer) < SHUFFLE_BUFFER_TARGET:
            pick = random.choice(self.library_tracks)
            if self.shuffle_buffer and pick["title"] == self.shuffle_buffer[-1]["title"]:
                continue  # avoid an immediate repeat
            self.shuffle_buffer.append(pick)

    def pop_next(self) -> dict | None:
        if self.manual:
            return self.manual.pop(0)
        if self.shuffle_active:
            self.refill_shuffle()
            if self.shuffle_buffer:
                return self.shuffle_buffer.pop(0)
        return None

    def upcoming(self, limit: int = 30) -> list[dict]:
        return (self.manual + self.shuffle_buffer)[:limit]


SHUFFLE_AUTOPLAY_DELAY_S = 4.0
RECONNECT_AUTOPLAY_DELAY_S = 1.5


async def try_autoplay(bus: Bus, q: Queue, log, delay: float = 0.0) -> None:
    if delay:
        await asyncio.sleep(delay)
    if q.held:
        return
    next_track = q.pop_next()
    if next_track:
        log.info("autoplay: %s", next_track["title"])
        await bus.publish(
            "jukebox/jb-source-phone/cmd",
            {"action": "play_item", "item_path": next_track["item_path"], "title": next_track["title"]},
        )
        q.expected_title = next_track["title"]
        q.playing = True
        await publish_queue(bus, q)


async def shuffle_autoplay(bus: Bus, q: Queue, log) -> None:
    """A few seconds after shuffle turns on, start it playing rather than
    waiting for whatever's already playing to end on its own -- gives
    immediate feedback that shuffle mode actually did something. No-ops if
    there's no phone to play through (a "connect device" notice already
    went out when shuffle was requested); the queue is left intact so it
    plays automatically once a phone actually connects (see the
    "connected" handler)."""
    await asyncio.sleep(SHUFFLE_AUTOPLAY_DELAY_S)
    # Something may have started in the meantime (e.g. a keypad pick right
    # after turning shuffle on) -- don't replace it.
    if not q.shuffle_active or not q.phone_connected or q.playing:
        return
    await try_autoplay(bus, q, log)


async def publish_queue(bus: Bus, q: Queue) -> None:
    await bus.publish(
        "jukebox/jb-core/queue",
        {"shuffle_active": q.shuffle_active, "upcoming": q.upcoming()},
        retain=True,
    )


async def publish_entry_events(bus: Bus, events, log) -> None:
    """Selection entry output -> bus. The kiosk turns a selected slot into
    the track currently shown there (it owns the board's page and sort) and
    sends it back as a normal "select" command."""
    for e in events:
        if isinstance(e, EntryChanged):
            await bus.publish("jukebox/jb-core/entry", e.payload(), retain=True)
        elif isinstance(e, Selection):
            log.info("keypad selection: %s (slot %d)", e.slot, e.index)
            await bus.publish("jukebox/jb-core/selection", e.payload())
        elif isinstance(e, Rejected):
            await bus.publish("jukebox/jb-core/rejected", e.payload())


POWER_COMMANDS = {"reboot": ["systemctl", "--no-ask-password", "reboot"],
                  "shutdown": ["systemctl", "--no-ask-password", "poweroff"]}


async def publish_power(bus: Bus, state: str, **extra) -> None:
    """jukebox/jb-core/power (retained): the UI shows the menu while
    state == "menu"; "rebooting" / "shutting_down" while going down."""
    await bus.publish("jukebox/jb-core/power", {"state": state, **extra}, retain=True)


async def apply_power(bus: Bus, q: Queue, actions: list, log) -> None:
    """Carries out what PowerMenu decided (see power.py for the meanings)."""
    for act in actions:
        if act == "skip":
            q.expected_title = None
            # The button lights blink Reset twice for this.
            await bus.publish("jukebox/jb-core/skipped", {"by": "reset"})
            next_track = q.pop_next()
            if next_track:
                log.info("Reset held: cancelling the song; next: %s", next_track["title"])
                await bus.publish("jukebox/jb-source-phone/cmd",
                                  {"action": "play_item", "item_path": next_track["item_path"],
                                   "title": next_track["title"]})
                q.expected_title, q.playing = next_track["title"], True
            else:
                log.info("Reset held: cancelling the song; nothing queued, stopping")
                # "stop", not "pause": the song is done with, and shows as
                # nothing playing (jb-source-phone keeps the phone paused).
                await bus.publish("jukebox/jb-source-phone/cmd", {"action": "stop"})
                q.playing = False
            await publish_queue(bus, q)
        elif act == "pause":
            await bus.publish("jukebox/jb-source-phone/cmd", {"action": "pause"})
        elif act == "open":
            q.held = True
            await publish_power(bus, "menu", until=time.time() + MENU_TIMEOUT_S)
        elif act == "close":
            q.held = False
            await publish_power(bus, "idle")
        elif act == "resume":
            log.info("power menu cancelled: resuming")
            await bus.publish("jukebox/jb-source-phone/cmd", {"action": "play"})
        elif act == "clear":
            q.manual, q.shuffle_buffer, q.shuffle_active = [], [], False
            await publish_queue(bus, q)
        elif act in POWER_COMMANDS:
            log.warning("power menu: %s", act)
            await bus.publish("jukebox/jb-source-phone/cmd", {"action": "pause"})
            await publish_power(bus, "rebooting" if act == "reboot" else "shutting_down")
            await asyncio.sleep(1.5)  # let the screen say so
            proc = await asyncio.create_subprocess_exec(*POWER_COMMANDS[act], stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode:
                log.error("%s failed: %s", act, err.decode().strip())
                await publish_power(bus, "idle", error=f"couldn't {act}")


async def power_ticks(bus: Bus, q: Queue, menu: PowerMenu, log) -> None:
    while True:
        await apply_power(bus, q, menu.tick(time.monotonic()), log)
        await asyncio.sleep(0.5)


async def entry_timeouts(bus: Bus, entry: Entry, log) -> None:
    while True:
        await publish_entry_events(bus, entry.tick(time.monotonic()), log)
        await asyncio.sleep(ENTRY_TICK_S)


async def stop_after_last_song(bus: Bus, q: Queue, guard: EndGuard, log) -> None:
    """With nothing queued, stop the playing song just before it ends, so
    iOS never moves on to a song nobody chose (end_guard.py)."""
    while True:
        await asyncio.sleep(0.1)
        title = guard.due(time.monotonic(), queue_empty=not q.upcoming() and not q.shuffle_active)
        if title:
            log.info("last song ending, stopping: %s", title)
            await bus.publish("jukebox/jb-source-phone/cmd", {"action": "stop"})
            q.playing = False
            q.expected_title = None


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    q = Queue()

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        messages = await bus.subscribe("jukebox/#")
        asyncio.create_task(bus.run_heartbeat())
        await publish_queue(bus, q)
        entry = Entry(ENTRY_TIMEOUT_MS)
        await bus.publish("jukebox/jb-core/entry", {"state": "idle", "letter": None, "reason": None}, retain=True)
        asyncio.create_task(entry_timeouts(bus, entry, log))
        menu = PowerMenu()
        await publish_power(bus, "idle")
        asyncio.create_task(power_ticks(bus, q, menu, log))
        guard = EndGuard()
        asyncio.create_task(stop_after_last_song(bus, q, guard, log))

        async for topic, payload in messages:
            if topic == "jukebox/jb-input/key":
                # Keypad letters and digits -> slot selections (letter first).
                await publish_entry_events(bus, entry.key(payload["key"], time.monotonic()), log)

            elif topic == "jukebox/jb-input/gesture":
                # Reset: a click cancels a waiting letter (the board mark and the
                # button lights both follow jukebox/jb-core/entry) or the power
                # menu; holding it cancels the song or opens the menu (power.py).
                if payload.get("control") == "reset":
                    gesture, now = payload.get("gesture"), time.monotonic()
                    if gesture == "click":
                        await apply_power(bus, q, menu.reset_click(), log)
                        await publish_entry_events(bus, entry.cancel(now), log)
                    elif gesture == "hold":
                        await apply_power(bus, q, menu.hold(now, q.playing), log)
                    elif gesture == "long_hold":
                        await apply_power(bus, q, menu.long_hold(now, q.playing), log)

            elif topic == "jukebox/jb-library/tracks":
                q.library_tracks = payload.get("tracks", [])

            elif topic == "jukebox/jb-source-phone/connected":
                was_connected = q.phone_connected
                q.phone_connected = bool(payload.get("connected"))
                # Don't fight whatever's already playing on a fresh
                # (re)connect -- re-establish baseline on the next
                # now-playing update instead of treating it as an
                # unexpected change to override.
                q.expected_title = None
                q.last_seen_title = None
                # If something got queued up while disconnected (a manual
                # pick, or shuffle turned on with nothing to play through),
                # start it now that a phone actually exists to play it on.
                if q.phone_connected and not was_connected and (
                    q.manual or (q.shuffle_active and q.shuffle_buffer)
                ):
                    asyncio.create_task(try_autoplay(bus, q, log, delay=RECONNECT_AUTOPLAY_DELAY_S))

            elif topic == "jukebox/jb-source-phone/play-error":
                # The commanded track never actually started (e.g. a stale
                # item_path from a library snapshot crawled before the last
                # AVRCP reconnect) -- don't sit believing it's "now playing"
                # forever. Drop the expectation and try the next queued track.
                log.warning("play_item failed on the bus, advancing queue: %r", payload.get("item_path"))
                q.expected_title = None
                q.playing = False
                await try_autoplay(bus, q, log)

            elif topic == "jukebox/jb-source-phone/now-playing":
                guard.update(payload, time.monotonic())
                q.playing = payload.get("status") == "playing"
                title = payload.get("title")
                if not title:
                    continue
                if q.last_seen_title is None:
                    # First observation since startup/reconnect: just
                    # establish the baseline, nothing to override yet.
                    q.last_seen_title = title
                    q.expected_title = title
                    continue
                if title == q.last_seen_title:
                    continue
                q.last_seen_title = title
                if q.expected_title is not None and title == q.expected_title:
                    continue  # our own commanded change taking effect
                # The track changed without our say-so (iOS auto-advanced
                # on its own) -- take over if we have anything queued.
                # While the power menu is open nothing new should play.
                next_track = None if q.held else q.pop_next()
                if next_track:
                    log.info("overriding auto-advance with queued track: %s", next_track["title"])
                    await bus.publish(
                        "jukebox/jb-source-phone/cmd",
                        {"action": "play_item", "item_path": next_track["item_path"], "title": next_track["title"]},
                    )
                    q.expected_title = next_track["title"]
                else:
                    # Nothing queued -- a jukebox with nothing left to play
                    # should stop, not silently keep going with whatever
                    # iOS auto-advanced to on its own (that's exactly the
                    # "iOS's queue is never used" principle this whole
                    # service exists to enforce).
                    # "stop", not "pause": the song iOS jumped to was never
                    # chosen, so it's paused AND cleared -- the screen shows
                    # nothing playing, as when Reset cancels the last song.
                    log.info("queue empty, stopping instead of letting iOS auto-advance run")
                    await bus.publish("jukebox/jb-source-phone/cmd", {"action": "stop"})
                    q.playing = False
                    q.expected_title = None
                await publish_queue(bus, q)

            elif topic == "jukebox/jb-core/cmd":
                action = payload.get("action")
                if action == "select":
                    track = payload.get("track")
                    if track:
                        q.manual.append(track)
                        log.info("queued manual selection: %s", track.get("title"))
                        if not q.phone_connected:
                            await bus.publish("jukebox/jb-core/notice", {"type": "connect_device"})
                            await publish_queue(bus, q)
                        elif not q.playing:
                            # Idle jukebox: a selection starts playing now,
                            # rather than waiting for a track to end.
                            await try_autoplay(bus, q, log)
                        else:
                            await publish_queue(bus, q)
                elif action == "select_album":
                    # Picking an album queues its songs in album order (the
                    # user's design); like a single pick, they start now if
                    # nothing is playing, else wait their turn.
                    tracks = [t for t in payload.get("tracks") or [] if t]
                    if tracks:
                        q.manual.extend(tracks)
                        log.info("queued album %r: %d songs", payload.get("album"), len(tracks))
                        if not q.phone_connected:
                            await bus.publish("jukebox/jb-core/notice", {"type": "connect_device"})
                            await publish_queue(bus, q)
                        elif not q.playing:
                            await try_autoplay(bus, q, log)
                        else:
                            await publish_queue(bus, q)
                elif action == "shuffle_on":
                    q.shuffle_active = True
                    q.refill_shuffle()
                    await publish_queue(bus, q)
                    if q.phone_connected:
                        asyncio.create_task(shuffle_autoplay(bus, q, log))
                    else:
                        await bus.publish("jukebox/jb-core/notice", {"type": "connect_device"})
                elif action == "shuffle_off":
                    q.shuffle_active = False
                    q.shuffle_buffer = []
                    await publish_queue(bus, q)
                elif action == "power":
                    await apply_power(bus, q, menu.choose(payload.get("choice", "")), log)
                else:
                    log.warning("unknown jb-core command: %r", action)


if __name__ == "__main__":
    asyncio.run(main())
