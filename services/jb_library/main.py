import asyncio
import json
import sqlite3
import time

import aiohttp

from shared import config, logging as jb_logging
from shared.bus import Bus

SERVICE_NAME = "jb-library"
DB_PATH = "/var/lib/juk3-box/library.db"

# Free, keyless API -- be polite about request rate. Enrichment is a one-time
# background cost per track (cached forever after), so slow-and-steady is fine.
ENRICH_DELAY_S = 1.5
ENRICH_RETRY_DELAY_S = 30  # after a failed lookup (e.g. no internet yet at boot)
ENRICH_PUBLISH_INTERVAL_S = 60


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tracks (
            phone_mac TEXT NOT NULL,
            title TEXT NOT NULL,
            item_path TEXT,
            artist TEXT,
            album TEXT,
            genre TEXT,
            artwork_url TEXT,
            enriched INTEGER DEFAULT 0,
            play_count INTEGER DEFAULT 0,
            last_played_at REAL,
            PRIMARY KEY (phone_mac, title)
        )
        """
    )
    conn.commit()
    return conn


def upsert_tracks(conn: sqlite3.Connection, mac: str, tracks: list) -> None:
    conn.executemany(
        """
        INSERT INTO tracks (phone_mac, title, item_path)
        VALUES (:mac, :title, :item_path)
        ON CONFLICT(phone_mac, title) DO UPDATE SET item_path = excluded.item_path
        """,
        [{"mac": mac, "title": t["title"], "item_path": t["item_path"]} for t in tracks],
    )
    conn.commit()


async def lookup_itunes(session: aiohttp.ClientSession, title: str) -> dict | None:
    params = {"term": title, "media": "music", "entity": "song", "limit": 1}
    async with session.get(
        "https://itunes.apple.com/search", params=params, timeout=aiohttp.ClientTimeout(total=10)
    ) as resp:
        if resp.status != 200:
            return None
        # itunes.apple.com returns Content-Type: text/javascript (a JSONP-era
        # leftover), not application/json -- aiohttp's strict check rejects
        # that by default.
        data = await resp.json(content_type=None)
        results = data.get("results", [])
        if not results:
            return None
        r = results[0]
        artwork = r.get("artworkUrl100", "")
        if artwork:
            artwork = artwork.replace("100x100bb", "600x600bb")
        return {
            "artist": r.get("artistName"),
            "album": r.get("collectionName"),
            "genre": r.get("primaryGenreName"),
            "artwork_url": artwork,
        }


class Snapshots:
    """Publishes the library snapshot, skipping it when nothing changed.

    The snapshot is the whole library in one retained message (~365KB for
    ~900 tracks) -- cheap to send once, but every subscriber (the kiosk UI
    especially) re-parses and re-renders all of it, so it must only go out
    when the data actually changed, never on a timer."""

    def __init__(self, bus: Bus, conn: sqlite3.Connection, log):
        self.bus = bus
        self.conn = conn
        self.log = log
        self._last_payload: str | None = None

    async def publish(self) -> None:
        """Whole library sorted by title -- the UI slices/sorts client-side."""
        rows = self.conn.execute(
            """
            SELECT title, artist, album, genre, artwork_url, item_path
            FROM tracks ORDER BY title COLLATE NOCASE
            """
        ).fetchall()
        tracks = [
            {
                "title": r[0], "artist": r[1], "album": r[2], "genre": r[3],
                "artwork_url": r[4], "item_path": r[5],
            }
            for r in rows
        ]
        payload = {"tracks": tracks}
        serialized = json.dumps(payload)
        if serialized == self._last_payload:
            return
        await self.bus.publish("jukebox/jb-library/tracks", payload, retain=True)
        self._last_payload = serialized
        self.log.info("published snapshot: %d tracks", len(tracks))


async def enrichment_loop(snapshots: Snapshots, wake: asyncio.Event, log) -> None:
    """Fills in artist/album/genre/art for un-enriched tracks, then sleeps
    until a new sync adds more (no polling). Needs internet, not the phone,
    so it also finishes any leftover work from before a restart."""
    conn = get_db()
    dirty = False
    last_publish = time.monotonic()
    async with aiohttp.ClientSession() as session:
        while True:
            row = conn.execute(
                "SELECT phone_mac, title FROM tracks WHERE enriched = 0 LIMIT 1"
            ).fetchone()
            if row is None:
                if dirty:
                    await snapshots.publish()
                    dirty = False
                    log.info("enrichment complete")
                wake.clear()
                await wake.wait()
                continue
            mac, title = row
            try:
                info = await lookup_itunes(session, title)
            except Exception:
                # A real failure (network, API hiccup) -- leave enriched=0
                # so it gets retried later instead of permanently giving up.
                log.exception("iTunes lookup failed for %r, will retry later", title)
                await asyncio.sleep(ENRICH_RETRY_DELAY_S)
                continue

            if info:
                conn.execute(
                    """
                    UPDATE tracks SET artist=:artist, album=:album, genre=:genre,
                        artwork_url=:artwork_url, enriched=1
                    WHERE phone_mac=:mac AND title=:title
                    """,
                    {**info, "mac": mac, "title": title},
                )
                dirty = True
            else:
                # Successful lookup, genuinely no match -- done, not a retry case.
                conn.execute(
                    "UPDATE tracks SET enriched=1 WHERE phone_mac=? AND title=?", (mac, title)
                )
            conn.commit()
            # A first-time sync of a big library takes ~20min to enrich --
            # show progress on the board occasionally, not per track.
            if dirty and time.monotonic() - last_publish >= ENRICH_PUBLISH_INTERVAL_S:
                await snapshots.publish()
                dirty = False
                last_publish = time.monotonic()
            await asyncio.sleep(ENRICH_DELAY_S)


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    conn = get_db()

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        # Subscribe first: crawl-result is retained, so if jb-source-phone
        # already synced a connected phone before we started, we get it now.
        messages = await bus.subscribe("jukebox/jb-source-phone/crawl-result")
        asyncio.create_task(bus.run_heartbeat())

        # Serve the cached library right away -- at boot there's usually no
        # phone connected yet, and we never ask for a crawl ourselves.
        # jb-source-phone (the connection manager) syncs when a phone connects.
        snapshots = Snapshots(bus, conn, log)
        await snapshots.publish()

        wake = asyncio.Event()
        wake.set()  # pick up any enrichment left over from before a restart
        asyncio.create_task(enrichment_loop(snapshots, wake, log))

        async for _topic, payload in messages:
            mac = payload.get("mac")
            tracks = payload.get("tracks")
            if not mac or tracks is None:
                continue
            upsert_tracks(conn, mac, tracks)
            total = conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE phone_mac=?", (mac,)
            ).fetchone()[0]
            log.info("stored %d tracks for %s (sync returned %d)", total, mac, len(tracks))
            await snapshots.publish()
            wake.set()


if __name__ == "__main__":
    asyncio.run(main())
