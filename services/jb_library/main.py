import asyncio
import sqlite3

import aiohttp

from shared import config, logging as jb_logging
from shared.bus import Bus

SERVICE_NAME = "jb-library"
DB_PATH = "/var/lib/juk3-box/library.db"

# Free, keyless API -- be polite about request rate. Enrichment is a one-time
# background cost per track (cached forever after), so slow-and-steady is fine.
ENRICH_DELAY_S = 1.5


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


async def enrichment_loop(log) -> None:
    conn = get_db()
    async with aiohttp.ClientSession() as session:
        while True:
            row = conn.execute(
                "SELECT phone_mac, title FROM tracks WHERE enriched = 0 LIMIT 1"
            ).fetchone()
            if row is None:
                await asyncio.sleep(10)
                continue
            mac, title = row
            try:
                info = await lookup_itunes(session, title)
            except Exception:
                # A real failure (network, API hiccup) -- leave enriched=0
                # so it gets retried later instead of permanently giving up.
                log.exception("iTunes lookup failed for %r, will retry later", title)
                await asyncio.sleep(ENRICH_DELAY_S)
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
            else:
                # Successful lookup, genuinely no match -- done, not a retry case.
                conn.execute(
                    "UPDATE tracks SET enriched=1 WHERE phone_mac=? AND title=?", (mac, title)
                )
            conn.commit()
            await asyncio.sleep(ENRICH_DELAY_S)


async def publish_snapshot(bus: Bus, conn: sqlite3.Connection, log) -> None:
    """Publishes the whole library, sorted by title. Small enough (under a
    thousand tracks, a couple hundred bytes each) to just send in one shot
    rather than building a page/sort request-response protocol yet -- the UI
    can slice/sort client-side."""
    rows = conn.execute(
        """
        SELECT title, artist, album, artwork_url, item_path
        FROM tracks ORDER BY title COLLATE NOCASE
        """
    ).fetchall()
    tracks = [
        {"title": r[0], "artist": r[1], "album": r[2], "artwork_url": r[3], "item_path": r[4]}
        for r in rows
    ]
    await bus.publish("jukebox/jb-library/tracks", {"tracks": tracks}, retain=True)
    log.info("published snapshot: %d tracks", len(tracks))


async def snapshot_loop(bus: Bus, conn: sqlite3.Connection, log) -> None:
    """Republishes periodically while enrichment is still filling in data,
    so the board visibly picks up artist/art as they arrive."""
    while True:
        await asyncio.sleep(20)
        await publish_snapshot(bus, conn, log)


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    conn = get_db()

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        asyncio.create_task(bus.run_heartbeat())
        asyncio.create_task(enrichment_loop(log))
        asyncio.create_task(snapshot_loop(bus, conn, log))
        await publish_snapshot(bus, conn, log)

        # Trigger a fresh crawl on our own startup too, in case a phone was
        # already connected before we started (the "connected" retained
        # message alone wouldn't tell us to crawl again).
        await bus.publish("jukebox/jb-source-phone/cmd", {"action": "crawl"})

        async for topic, payload in await bus.subscribe("jukebox/jb-source-phone/#"):
            if topic.endswith("/connected") and payload.get("connected"):
                log.info("phone connected (%s), requesting crawl", payload.get("name"))
                await bus.publish("jukebox/jb-source-phone/cmd", {"action": "crawl"})
            elif topic.endswith("/crawl-result"):
                mac = payload["mac"]
                tracks = payload["tracks"]
                upsert_tracks(conn, mac, tracks)
                total = conn.execute(
                    "SELECT COUNT(*) FROM tracks WHERE phone_mac=?", (mac,)
                ).fetchone()[0]
                log.info("stored %d tracks for %s (crawl returned %d)", total, mac, len(tracks))
                await publish_snapshot(bus, conn, log)


if __name__ == "__main__":
    asyncio.run(main())
