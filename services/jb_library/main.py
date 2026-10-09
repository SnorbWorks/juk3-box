import asyncio
import json
import re
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
# Last.fm asks API clients to stay under 5 requests/second.
LASTFM_DELAY_S = 0.25
# A track counts as "played" once it has actually played this long (the
# same rule Last.fm scrobbling uses) -- so skips, and the moment iOS starts
# its own auto-advance pick before jb-core overrides it, don't count.
PLAY_COUNT_THRESHOLD_MS = 30_000

# Columns added after the table was first created -- applied in place so an
# existing library.db keeps its data.
MIGRATIONS = {
    "lastfm_listeners": "INTEGER",            # global popularity: distinct Last.fm listeners
    "popularity_checked": "INTEGER DEFAULT 0",
    # When a song first showed up in a sync after this phone's first one
    # (the UI's "New"). NULL for songs that were there from the start.
    "added_at": "REAL",
}


# A sync smaller than this fraction of the stored library is treated as a
# glitch (e.g. Bluetooth dropping mid-crawl) and doesn't remove anything.
PRUNE_MIN_FRACTION = 0.5

TRACKS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS {name} (
        phone_mac TEXT NOT NULL,
        title TEXT NOT NULL,
        -- Which copy of this title, in the phone's library order (0 = first).
        -- AVRCP browsing gives titles only and no stable per-track ID, so two
        -- different songs can share a title (22 such pairs were seen on the
        -- user's phone); (title, dup) keeps them both.
        dup INTEGER NOT NULL DEFAULT 0,
        item_path TEXT,
        artist TEXT,
        album TEXT,
        genre TEXT,
        artwork_url TEXT,
        enriched INTEGER DEFAULT 0,
        play_count INTEGER DEFAULT 0,
        last_played_at REAL,
        lastfm_listeners INTEGER,
        popularity_checked INTEGER DEFAULT 0,
        added_at REAL,
        PRIMARY KEY (phone_mac, title, dup)
    )
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(TRACKS_SCHEMA.format(name="tracks"))
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    for column, decl in MIGRATIONS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {column} {decl}")
    if "dup" not in existing:
        # Old schema keyed on (phone_mac, title): rebuild with the dup column
        # in the key (SQLite can't change a primary key in place). Existing
        # rows keep their enrichment and play counts as dup 0.
        conn.execute(TRACKS_SCHEMA.format(name="tracks_v2"))
        conn.execute(
            """
            INSERT INTO tracks_v2 (phone_mac, title, dup, item_path, artist, album, genre,
                artwork_url, enriched, play_count, last_played_at, lastfm_listeners, popularity_checked)
            SELECT phone_mac, title, 0, item_path, artist, album, genre, artwork_url, enriched,
                play_count, last_played_at, lastfm_listeners, popularity_checked FROM tracks
            """
        )
        conn.execute("DROP TABLE tracks")
        conn.execute("ALTER TABLE tracks_v2 RENAME TO tracks")
    conn.commit()
    return conn


def number_duplicates(tracks: list) -> list:
    """[(title, dup, item_path)] in crawl order: the Nth copy of a title gets dup N-1."""
    seen = {}
    out = []
    for t in tracks:
        dup = seen.get(t["title"], 0)
        seen[t["title"]] = dup + 1
        out.append((t["title"], dup, t["item_path"]))
    return out


def upsert_tracks(conn: sqlite3.Connection, mac: str, tracks: list, log=None, now: float | None = None) -> int:
    """Stores a sync for one phone and removes songs no longer on it (so no
    board entry is left pointing at a play path that now belongs to another
    song). Songs this sync adds to an already-synced phone are stamped with
    `added_at`. Returns how many were removed."""
    numbered = number_duplicates(tracks)
    had_songs = conn.execute("SELECT 1 FROM tracks WHERE phone_mac=? LIMIT 1", (mac,)).fetchone() is not None
    added_at = (now if now is not None else time.time()) if had_songs else None
    conn.executemany(
        """
        INSERT INTO tracks (phone_mac, title, dup, item_path, added_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(phone_mac, title, dup) DO UPDATE SET item_path = excluded.item_path
        """,
        [(mac, title, dup, path, added_at) for title, dup, path in numbered],
    )
    stored = conn.execute("SELECT COUNT(*) FROM tracks WHERE phone_mac=?", (mac,)).fetchone()[0]
    removed = 0
    if len(numbered) >= stored * PRUNE_MIN_FRACTION:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS synced (title TEXT, dup INTEGER)")
        conn.execute("DELETE FROM synced")
        conn.executemany("INSERT INTO synced VALUES (?, ?)", [(t, d) for t, d, _ in numbered])
        removed = conn.execute(
            """
            DELETE FROM tracks WHERE phone_mac = ?
              AND NOT EXISTS (SELECT 1 FROM synced s WHERE s.title = tracks.title AND s.dup = tracks.dup)
            """,
            (mac,),
        ).rowcount
    elif log:
        log.warning("sync returned %d tracks but %d are stored -- not removing anything",
                    len(numbered), stored)
    conn.commit()
    return removed


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


def _search_terms(title: str, artist: str) -> tuple[str, str]:
    """Strip "(feat. X)" / "[Remix]"-style extras from the title and keep only
    the lead artist -- confirmed live to take Last.fm matching from 16/20 to
    19/20 on a sample of this library."""
    clean_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip() or title
    main_artist = re.split(r" feat\.? | featuring | & |, | and ", artist, flags=re.I)[0]
    return clean_title, main_artist


async def lookup_lastfm(session: aiohttp.ClientSession, api_key: str, title: str, artist: str) -> int | None:
    """Returns the song's global Last.fm listener count, or None if Last.fm
    has no such track. Raises on network/API failure (caller retries).
    track.getInfo aggregates every version of a song and autocorrects
    spelling, unlike per-recording scores (Deezer's rank was tried first and
    was noticeably noisier)."""
    clean_title, main_artist = _search_terms(title, artist)
    params = {
        "method": "track.getInfo", "api_key": api_key, "format": "json",
        "track": clean_title, "artist": main_artist, "autocorrect": 1,
    }
    async with session.get(
        "https://ws.audioscrobbler.com/2.0/", params=params, timeout=aiohttp.ClientTimeout(total=10)
    ) as resp:
        data = await resp.json(content_type=None)
    if "track" in data:
        return int(data["track"].get("listeners") or 0)
    if data.get("error") == 6:  # "Track not found" -- a real answer, not a failure
        return None
    raise RuntimeError(f"Last.fm error: {data.get('error')} {data.get('message')}")


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
            SELECT title, artist, album, genre, artwork_url, item_path,
                   lastfm_listeners, play_count, last_played_at, added_at
            FROM tracks ORDER BY title COLLATE NOCASE
            """
        ).fetchall()
        tracks = [
            {
                "title": r[0], "artist": r[1], "album": r[2], "genre": r[3],
                "artwork_url": r[4], "item_path": r[5],
                "popularity": r[6], "play_count": r[7] or 0, "last_played_at": r[8],
                "added_at": r[9],
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


async def enrichment_loop(snapshots: Snapshots, wake: asyncio.Event, lastfm_key: str | None, log) -> None:
    """Background enrichment, in two passes per track, then sleeps until a
    new sync adds more (no polling). Needs internet, not the phone, so it
    also finishes any leftover work from before a restart.
      1. iTunes: artist/album/genre/art (needs only the title).
      2. Last.fm: global popularity (needs the artist from pass 1). Skipped
         entirely if no lastfm_api_key is configured."""
    conn = get_db()
    dirty = False
    last_publish = time.monotonic()
    if not lastfm_key:
        log.warning("no lastfm_api_key configured -- popularity (Top 100) lookups disabled")
    async with aiohttp.ClientSession() as session:
        while True:
            # A first-time sync of a big library takes a while to enrich --
            # show progress on the board occasionally, not per track.
            if dirty and time.monotonic() - last_publish >= ENRICH_PUBLISH_INTERVAL_S:
                await snapshots.publish()
                dirty = False
                last_publish = time.monotonic()

            row = conn.execute(
                "SELECT phone_mac, title FROM tracks WHERE enriched = 0 LIMIT 1"
            ).fetchone()
            if row is not None:
                dirty |= await _enrich_itunes(conn, session, *row, log)
                await asyncio.sleep(ENRICH_DELAY_S)
                continue

            row = conn.execute(
                "SELECT phone_mac, title, artist FROM tracks"
                " WHERE enriched = 1 AND popularity_checked = 0 LIMIT 1"
            ).fetchone() if lastfm_key else None
            if row is not None:
                dirty |= await _enrich_lastfm(conn, session, lastfm_key, *row, log)
                await asyncio.sleep(LASTFM_DELAY_S)
                continue

            if dirty:
                await snapshots.publish()
                dirty = False
                last_publish = time.monotonic()
                log.info("enrichment complete")
            wake.clear()
            await wake.wait()


async def _enrich_itunes(conn, session, mac: str, title: str, log) -> bool:
    """Returns whether any displayed data changed."""
    try:
        info = await lookup_itunes(session, title)
    except Exception:
        # A real failure (network, API hiccup) -- leave enriched=0
        # so it gets retried later instead of permanently giving up.
        log.exception("iTunes lookup failed for %r, will retry later", title)
        await asyncio.sleep(ENRICH_RETRY_DELAY_S)
        return False
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
        conn.execute("UPDATE tracks SET enriched=1 WHERE phone_mac=? AND title=?", (mac, title))
    conn.commit()
    return bool(info)


async def _enrich_lastfm(conn, session, api_key: str, mac: str, title: str, artist: str | None, log) -> bool:
    """Returns whether any displayed data changed."""
    listeners = None
    if artist:  # without an artist there's nothing reliable to look up
        try:
            listeners = await lookup_lastfm(session, api_key, title, artist)
        except Exception:
            log.exception("Last.fm lookup failed for %r, will retry later", title)
            await asyncio.sleep(ENRICH_RETRY_DELAY_S)
            return False
    conn.execute(
        "UPDATE tracks SET lastfm_listeners=?, popularity_checked=1 WHERE phone_mac=? AND title=?",
        (listeners, mac, title),
    )
    conn.commit()
    return listeners is not None


class PlayTracker:
    """Counts jukebox plays from jb-source-phone's now-playing stream.

    One play = one continuous playthrough that reaches PLAY_COUNT_THRESHOLD_MS.
    A new playthrough starts when the title changes or the position jumps
    backwards (the same song restarted or replayed)."""

    def __init__(self, conn: sqlite3.Connection, log):
        self.conn = conn
        self.log = log
        self.mac: str | None = None
        self._title: str | None = None
        self._last_pos = 0
        self._counted = False
        self._first_update = True

    def on_connected(self, payload: dict) -> None:
        self.mac = payload.get("mac") if payload.get("connected") else None

    def on_now_playing(self, np: dict) -> bool:
        """Returns True when this update recorded a play."""
        title, pos = np.get("title"), np.get("position_ms") or 0
        if self._first_update:
            # Joined mid-song (e.g. this service restarted on a deploy): that
            # playthrough may already have been counted before the restart,
            # so don't count it again -- an occasional missed count beats
            # inflating one on every restart.
            self._first_update = False
            self._title, self._last_pos = title, pos
            self._counted = pos >= PLAY_COUNT_THRESHOLD_MS
            return False
        if title != self._title or pos < self._last_pos - 5_000:
            self._title, self._counted = title, False
        self._last_pos = pos
        if (self._counted or not title or not self.mac
                or np.get("status") != "playing" or pos < PLAY_COUNT_THRESHOLD_MS):
            return False
        self._counted = True
        dup = self._which_copy(title, np.get("artist"))
        if dup is None:
            return False
        self.conn.execute(
            "UPDATE tracks SET play_count = play_count + 1, last_played_at = ?"
            " WHERE phone_mac = ? AND title = ? AND dup = ?",
            (time.time(), self.mac, title, dup),
        )
        self.conn.commit()
        self.log.info("counted play: %s (copy %d)", title, dup)
        return True

    def _which_copy(self, title, artist):
        """With duplicate titles, count the copy whose artist matches what's
        actually playing; otherwise the first copy. None if not in the library."""
        rows = self.conn.execute(
            "SELECT dup, artist FROM tracks WHERE phone_mac = ? AND title = ? ORDER BY dup",
            (self.mac, title),
        ).fetchall()
        if not rows:
            return None
        for dup, stored_artist in rows:
            if artist and stored_artist and stored_artist.casefold() == artist.casefold():
                return dup
        return rows[0][0]


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    conn = get_db()

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        # Subscribe first: crawl-result and connected are retained, so if
        # jb-source-phone already synced a connected phone before we started,
        # we get it now. (All subscriptions share one message stream.)
        messages = await bus.subscribe("jukebox/jb-source-phone/crawl-result")
        await bus.subscribe("jukebox/jb-source-phone/connected")
        await bus.subscribe("jukebox/jb-source-phone/now-playing")
        asyncio.create_task(bus.run_heartbeat())

        # Serve the cached library right away -- at boot there's usually no
        # phone connected yet, and we never ask for a crawl ourselves.
        # jb-source-phone (the connection manager) syncs when a phone connects.
        snapshots = Snapshots(bus, conn, log)
        await snapshots.publish()

        wake = asyncio.Event()
        wake.set()  # pick up any enrichment left over from before a restart
        asyncio.create_task(enrichment_loop(snapshots, wake, cfg.get("lastfm_api_key"), log))
        plays = PlayTracker(conn, log)

        async for topic, payload in messages:
            if topic.endswith("/connected"):
                plays.on_connected(payload)
                continue
            if topic.endswith("/now-playing"):
                if plays.on_now_playing(payload):
                    await snapshots.publish()  # once per counted play
                continue
            mac = payload.get("mac")
            tracks = payload.get("tracks")
            if not mac or tracks is None:
                continue
            removed = upsert_tracks(conn, mac, tracks, log)
            total = conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE phone_mac=?", (mac,)
            ).fetchone()[0]
            log.info("stored %d tracks for %s (sync returned %d, removed %d no longer on the phone)",
                     total, mac, len(tracks), removed)
            await snapshots.publish()
            wake.set()


if __name__ == "__main__":
    asyncio.run(main())
