import asyncio
import random

from shared import config, logging as jb_logging
from shared.bus import Bus

SERVICE_NAME = "jb-core"

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


async def publish_queue(bus: Bus, q: Queue) -> None:
    await bus.publish(
        "jukebox/jb-core/queue",
        {"shuffle_active": q.shuffle_active, "upcoming": q.upcoming()},
        retain=True,
    )


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    q = Queue()

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        messages = await bus.subscribe("jukebox/#")
        asyncio.create_task(bus.run_heartbeat())
        await publish_queue(bus, q)

        async for topic, payload in messages:
            if topic == "jukebox/jb-library/tracks":
                q.library_tracks = payload.get("tracks", [])

            elif topic == "jukebox/jb-source-phone/connected":
                # Don't fight whatever's already playing on a fresh
                # (re)connect -- re-establish baseline on the next
                # now-playing update instead of treating it as an
                # unexpected change to override.
                q.expected_title = None
                q.last_seen_title = None

            elif topic == "jukebox/jb-source-phone/now-playing":
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
                # on its own) -- take over if we have anything queued,
                # otherwise just accept it as the new baseline.
                next_track = q.pop_next()
                if next_track:
                    log.info("overriding auto-advance with queued track: %s", next_track["title"])
                    await bus.publish(
                        "jukebox/jb-source-phone/cmd",
                        {"action": "play_item", "item_path": next_track["item_path"]},
                    )
                    q.expected_title = next_track["title"]
                else:
                    q.expected_title = title
                await publish_queue(bus, q)

            elif topic == "jukebox/jb-core/cmd":
                action = payload.get("action")
                if action == "select":
                    track = payload.get("track")
                    if track:
                        q.manual.append(track)
                        log.info("queued manual selection: %s", track.get("title"))
                        await publish_queue(bus, q)
                elif action == "shuffle_on":
                    q.shuffle_active = True
                    q.refill_shuffle()
                    await publish_queue(bus, q)
                elif action == "shuffle_off":
                    q.shuffle_active = False
                    q.shuffle_buffer = []
                    await publish_queue(bus, q)
                else:
                    log.warning("unknown jb-core command: %r", action)


if __name__ == "__main__":
    asyncio.run(main())
