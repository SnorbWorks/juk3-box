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
    # Marked by the owner from the Now Playing overlay.
    "favorite": "INTEGER DEFAULT 0",
    # The phone's own artist/album/genre, from jb-crawl's deep crawl (which
    # folder a title sits in). Shown in preference to the iTunes guesses in
    # artist/album/genre above, which stay as the fallback.
    "phone_artist": "TEXT",
    "phone_album": "TEXT",
    "phone_genre": "TEXT",
    "album_track": "INTEGER",   # 1-based position in phone_album
}

# Deep-crawl tables (jb-crawl). Stored by title, never by play path: play
# paths die on every reconnect, titles don't.
DEEP_SCHEMA = """
    CREATE TABLE IF NOT EXISTS playlists (
        phone_mac TEXT NOT NULL,
        name TEXT NOT NULL,
        position INTEGER NOT NULL,
        title TEXT NOT NULL,
        PRIMARY KEY (phone_mac, name, position)
    );
    -- The phone's Albums > Recently Added order (0 = newest): the true
    -- order for the UI's New row.
    CREATE TABLE IF NOT EXISTS album_recent (
        phone_mac TEXT NOT NULL,
        album TEXT NOT NULL,
        rank INTEGER NOT NULL,
        PRIMARY KEY (phone_mac, album)
    );
    -- Every album on the phone (names only, the phone's order): what the
    -- board's Albums view lists. Contents come later, on demand. Keyed by
    -- position: names repeat (7 pairs on the user's phone, e.g. two
    -- "Greatest Hits"), and read_albums tells them apart.
    CREATE TABLE IF NOT EXISTS album_index (
        phone_mac TEXT NOT NULL,
        position INTEGER NOT NULL,
        name TEXT NOT NULL,
        PRIMARY KEY (phone_mac, position)
    );
    -- An album's songs, fetched when someone first opens it, under its key
    -- (read_albums: the name, or "Name (2)" for a repeated one). uid is the
    -- phone's ID for the song, the same as in Songs, so it matches the
    -- end of the library's play path exactly (unlike titles, which repeat).
    CREATE TABLE IF NOT EXISTS album_tracks (
        phone_mac TEXT NOT NULL,
        album TEXT NOT NULL,
        position INTEGER NOT NULL,
        title TEXT NOT NULL,
        uid TEXT NOT NULL,
        PRIMARY KEY (phone_mac, album, position)
    );
"""


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
        favorite INTEGER DEFAULT 0,
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
    # album_index used to be keyed by name, which merged albums sharing one;
    # rebuild it (it's refilled by jb-crawl's daily album list).
    pk = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(album_index)")}
    if pk.get("name"):
        conn.execute("DROP TABLE album_index")
    conn.executescript(DEEP_SCHEMA)
    conn.commit()
    return conn


def _assign_by_title(conn, mac: str, title_to_values: dict, column: str) -> int:
    """Set `column` on each copy of each title. A title can sit under several
    values (two different songs sharing a name, by different artists): the
    Nth copy gets the Nth value, the best that title-only data allows.
    Returns rows changed."""
    changed = 0
    for title, values in title_to_values.items():
        dups = [r[0] for r in conn.execute(
            "SELECT dup FROM tracks WHERE phone_mac=? AND title=? ORDER BY dup", (mac, title))]
        for i, dup in enumerate(dups):
            value = values[min(i, len(values) - 1)]
            changed += conn.execute(
                f"UPDATE tracks SET {column}=? WHERE phone_mac=? AND title=? AND dup=?"
                f" AND {column} IS NOT ?", (value, mac, title, dup, value)).rowcount
    return changed


def store_deep(conn: sqlite3.Connection, mac: str, branch: str, data: dict) -> int:
    """Stores one branch of jb-crawl's deep crawl for a phone, replacing what
    that branch stored before. Returns how many rows changed. Raises
    ValueError on data it can't use (jb-crawl then crawls that branch again)."""
    changed = 0
    if branch == "playlists":
        playlists = data["playlists"]
        conn.execute("DELETE FROM playlists WHERE phone_mac=?", (mac,))
        conn.executemany(
            "INSERT INTO playlists (phone_mac, name, position, title) VALUES (?, ?, ?, ?)",
            [(mac, name, i, title) for name, titles in playlists.items() for i, title in enumerate(titles)])
        changed = len(playlists)
    elif branch == "artists":
        by_title = {}
        for artist, titles in data["artists"].items():
            for t in titles:
                by_title.setdefault(t, []).append(artist)
        before = dict(((r[0], r[1]), r[2]) for r in conn.execute(
            "SELECT title, dup, phone_artist FROM tracks WHERE phone_mac=?", (mac,)))
        changed = _assign_by_title(conn, mac, by_title, "phone_artist")
        # A song whose real artist is now known (or changed) gets its cover
        # and popularity looked up again, by artist + title this time.
        for title, dup, artist in conn.execute(
                "SELECT title, dup, phone_artist FROM tracks WHERE phone_mac=?", (mac,)).fetchall():
            if artist and before.get((title, dup)) != artist:
                conn.execute("UPDATE tracks SET enriched=0, popularity_checked=0"
                             " WHERE phone_mac=? AND title=? AND dup=?", (mac, title, dup))
    elif branch == "albums":
        by_title, track_no = {}, {}
        for album, titles in data["albums"].items():
            for i, t in enumerate(titles, 1):
                by_title.setdefault(t, []).append(album)
                track_no.setdefault(t, []).append(i)
        changed = _assign_by_title(conn, mac, by_title, "phone_album")
        changed += _assign_by_title(conn, mac, track_no, "album_track")
        conn.execute("DELETE FROM album_recent WHERE phone_mac=?", (mac,))
        conn.executemany("INSERT OR IGNORE INTO album_recent (phone_mac, album, rank) VALUES (?, ?, ?)",
                         [(mac, album, i) for i, album in enumerate(data.get("recent", []))])
    elif branch == "album_index":
        names = data["albums"]
        conn.execute("DELETE FROM album_index WHERE phone_mac=?", (mac,))
        conn.executemany("INSERT INTO album_index (phone_mac, position, name) VALUES (?, ?, ?)",
                         [(mac, i, n) for i, n in enumerate(names)])
        conn.execute("DELETE FROM album_recent WHERE phone_mac=?", (mac,))
        conn.executemany("INSERT OR IGNORE INTO album_recent (phone_mac, album, rank) VALUES (?, ?, ?)",
                         [(mac, a, i) for i, a in enumerate(data.get("recent", []))])
        changed = len(names)
    elif branch == "album":
        name, tracks = data["name"], data["tracks"]
        conn.execute("DELETE FROM album_tracks WHERE phone_mac=? AND album=?", (mac, name))
        conn.executemany(
            "INSERT INTO album_tracks (phone_mac, album, position, title, uid) VALUES (?, ?, ?, ?, ?)",
            [(mac, name, i, t["title"], str(t["uid"])) for i, t in enumerate(tracks)])
        # The songs themselves now know their real album and track number.
        for i, t in enumerate(tracks, 1):
            changed += conn.execute(
                "UPDATE tracks SET phone_album=?, album_track=? WHERE phone_mac=? AND item_path LIKE ?",
                (name, i, mac, f"%/item{t['uid']}")).rowcount
    elif branch == "genres":
        genre_of = {}
        for genre, artists in data["genres"].items():
            for a in artists:
                genre_of.setdefault(a.casefold(), genre)   # an artist's first genre
        for title, dup, artist in conn.execute(
                "SELECT title, dup, COALESCE(phone_artist, artist) FROM tracks WHERE phone_mac=?", (mac,)).fetchall():
            genre = genre_of.get((artist or "").casefold())
            if genre:
                changed += conn.execute(
                    "UPDATE tracks SET phone_genre=? WHERE phone_mac=? AND title=? AND dup=? AND phone_genre IS NOT ?",
                    (genre, mac, title, dup, genre)).rowcount
    else:
        raise ValueError(f"unknown branch {branch!r}")
    conn.commit()
    return changed


def album_key(title: str, nth: int) -> str:
    """An album's key: its name, or "Name (2)" for the second album with
    that name (nth counts from 0), so albums sharing a name stay apart."""
    return title if nth == 0 else f"{title} ({nth + 1})"


def read_albums(conn: sqlite3.Connection, mac: str | None = None) -> dict:
    """{albums: [{name (the key), title, nth, rank}] in the phone's order,
    contents: {key: [{title, uid}] in album order}} -- contents only for
    albums opened so far. nth = which album of that title it is (0 = first),
    what jb-crawl needs to open the right one."""
    albums, seen = [], {}
    for title, rank in conn.execute(
            "SELECT i.name, r.rank FROM album_index i LEFT JOIN album_recent r"
            " ON r.phone_mac = i.phone_mac AND r.album = i.name"
            " WHERE ? IS NULL OR i.phone_mac = ? ORDER BY i.phone_mac, i.position", (mac, mac)):
        nth = seen.get(title, 0)
        seen[title] = nth + 1
        albums.append({"name": album_key(title, nth), "title": title, "nth": nth, "rank": rank})
    contents = {}
    for album, title, uid in conn.execute(
            "SELECT album, title, uid FROM album_tracks WHERE ? IS NULL OR phone_mac = ?"
            " ORDER BY phone_mac, album, position", (mac, mac)):
        contents.setdefault(album, []).append({"title": title, "uid": uid})
    return {"albums": albums, "contents": contents}


def read_playlists(conn: sqlite3.Connection, mac: str | None = None) -> list:
    """[{name, titles}] for one phone (or every phone), in the phone's playlist order."""
    out = {}
    for name, title in conn.execute(
            "SELECT name, title FROM playlists WHERE ? IS NULL OR phone_mac = ? ORDER BY phone_mac, rowid",
            (mac, mac)):
        out.setdefault(name, []).append(title)
    return [{"name": n, "titles": t} for n, t in out.items()]


def forget_phone(conn: sqlite3.Connection, mac: str) -> int:
    """Removes everything stored about one phone (it was forgotten): its
    songs, playlists and albums, with their counts and favorites."""
    removed = 0
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        columns = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
        if "phone_mac" in columns:
            removed += conn.execute(f"DELETE FROM {table} WHERE phone_mac = ?", (mac,)).rowcount
    conn.commit()
    return removed


def latest_phone(conn: sqlite3.Connection) -> str | None:
    """The phone whose music was in use most recently: what the board shows
    when no phone is connected (at boot, before it reconnects)."""
    row = conn.execute(
        "SELECT phone_mac FROM tracks WHERE phone_mac NOT LIKE 'drive:%' GROUP BY phone_mac"
        " ORDER BY MAX(COALESCE(last_played_at, 0)) DESC, MAX(COALESCE(added_at, 0)) DESC LIMIT 1").fetchone()
    return row[0] if row else None


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


def store_drive(conn: sqlite3.Connection, drive_id: str, tracks: list, log=None) -> int:
    """Stores a USB drive's songs (jb-source-local) under phone_mac
    "drive:<id>". Its tags are exact, so they go where a phone's deep crawl
    puts what the phone says (phone_artist/album/genre, album_track): they
    win over the iTunes guesses, which still bring the covers. Returns how
    many songs were removed (no longer on the drive)."""
    key = f"drive:{drive_id}"
    removed = upsert_tracks(conn, key, tracks, log)
    numbered = number_duplicates(tracks)
    by_key = {(t["title"], dup): t for t, (_, dup, _) in zip(tracks, numbered)}
    conn.executemany(
        """
        UPDATE tracks SET phone_artist = ?, phone_album = ?, phone_genre = ?, album_track = ?,
               enriched = CASE WHEN COALESCE(phone_artist, '') = COALESCE(?, '') THEN enriched ELSE 0 END
        WHERE phone_mac = ? AND title = ? AND dup = ?
        """,
        [(t.get("artist"), t.get("album"), t.get("genre"), t.get("album_track"), t.get("artist"),
          key, title, dup) for (title, dup), t in by_key.items()],
    )
    # No artist tag: a lookup by title alone is a guess (a jazz "After Hours"
    # came back as The Weeknd). Better a plain cover than a wrong one.
    conn.execute(
        """
        UPDATE tracks SET enriched = 1, popularity_checked = 1, artist = NULL, album = NULL, genre = NULL,
               artwork_url = NULL, lastfm_listeners = NULL
        WHERE phone_mac = ? AND phone_artist IS NULL
        """, (key,))
    conn.commit()
    return removed


async def lookup_itunes(session: aiohttp.ClientSession, title: str, artist: str | None = None) -> dict | None:
    """Best iTunes match for a song. With the phone's real artist known,
    searches title + artist and prefers a result by that artist (title-only
    searches pick a different song with the same name too often)."""
    term = f"{title} {_search_terms(title, artist)[1]}" if artist else title
    params = {"term": term, "media": "music", "entity": "song", "limit": 5 if artist else 1}
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
        if artist:
            lead = _search_terms(title, artist)[1].casefold()
            r = next((x for x in results if lead in (x.get("artistName") or "").casefold()), r)
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
        self._last_playlists: str | None = None
        self._last_albums: str | None = None
        # Whose music: the connected phone's, else the last one in use.
        # Only one phone's library at a time -- another phone's songs
        # can't be played on this one.
        self.mac: str | None = latest_phone(conn)
        # USB drives plugged in now (jb-source-local): their songs are
        # always shown too, alongside the phone's (stored as phone_mac
        # "drive:<id>"). A pulled drive's rows stay, for its play counts.
        self.drives: list[str] = []

    async def publish(self) -> None:
        """Whole library sorted by title -- the UI slices/sorts client-side.
        artist/album/genre are the phone's own (deep crawl) where known, the
        iTunes guess otherwise. album_track = position in the album;
        album_rank = the album's place in the phone's Recently Added (0 =
        newest), for ordering the New row. Also publishes the playlists."""
        rows = self.conn.execute(
            """
            SELECT t.title, COALESCE(t.phone_artist, t.artist), COALESCE(t.phone_album, t.album),
                   COALESCE(t.phone_genre, t.genre), t.artwork_url, t.item_path,
                   t.lastfm_listeners, t.play_count, t.last_played_at, t.added_at, t.favorite,
                   t.album_track, r.rank
            FROM tracks t
            -- By the phone's album name where the deep crawl gave one, else the
            -- lookup's (they're the same for most albums): the phone's Recently
            -- Added order reaches far more songs than the crawled ones.
            LEFT JOIN album_recent r ON r.phone_mac = t.phone_mac AND r.album = COALESCE(t.phone_album, t.album)
            WHERE (t.phone_mac NOT LIKE 'drive:%' AND (? IS NULL OR t.phone_mac = ?))
               OR t.phone_mac IN (SELECT value FROM json_each(?))
            ORDER BY t.title COLLATE NOCASE
            """, (self.mac, self.mac, json.dumps(self.drives))
        ).fetchall()
        tracks = [
            {
                "title": r[0], "artist": r[1], "album": r[2], "genre": r[3],
                "artwork_url": r[4], "item_path": r[5],
                "popularity": r[6], "play_count": r[7] or 0, "last_played_at": r[8],
                "added_at": r[9], "favorite": bool(r[10]),
                "album_track": r[11], "album_rank": r[12],
            }
            for r in rows
        ]
        payload = {"tracks": tracks}
        serialized = json.dumps(payload)
        if serialized != self._last_payload:
            await self.bus.publish("jukebox/jb-library/tracks", payload, retain=True)
            self._last_payload = serialized
            self.log.info("published snapshot: %d tracks", len(tracks))
        playlists = {"playlists": read_playlists(self.conn, self.mac)}
        serialized = json.dumps(playlists)
        if serialized != self._last_playlists:
            await self.bus.publish("jukebox/jb-library/playlists", playlists, retain=True)
            self._last_playlists = serialized
            self.log.info("published %d playlists", len(playlists["playlists"]))
        albums = read_albums(self.conn, self.mac)
        serialized = json.dumps(albums)
        if serialized != self._last_albums:
            await self.bus.publish("jukebox/jb-library/albums", albums, retain=True)
            self._last_albums = serialized
            self.log.info("published %d albums (%d opened)", len(albums["albums"]), len(albums["contents"]))


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
                "SELECT phone_mac, title, dup, phone_artist FROM tracks WHERE enriched = 0 LIMIT 1"
            ).fetchone()
            if row is not None:
                mac, title, dup, phone_artist = row
                dirty |= await _enrich_itunes(conn, session, mac, title, log, dup, phone_artist)
                await asyncio.sleep(ENRICH_DELAY_S)
                continue

            row = conn.execute(
                "SELECT phone_mac, title, COALESCE(phone_artist, artist) FROM tracks"
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


async def _enrich_itunes(conn, session, mac: str, title: str, log, dup: int = 0,
                         phone_artist: str | None = None) -> bool:
    """Returns whether any displayed data changed. Without the phone's
    artist, every copy of the title gets the same title-only match (as
    before); with it, just this copy, matched by artist + title."""
    where = "phone_mac=:mac AND title=:title" + (" AND dup=:dup" if phone_artist else "")
    try:
        info = await lookup_itunes(session, title, phone_artist)
    except Exception:
        # A real failure (network, API hiccup) -- leave enriched=0
        # so it gets retried later instead of permanently giving up.
        log.exception("iTunes lookup failed for %r, will retry later", title)
        await asyncio.sleep(ENRICH_RETRY_DELAY_S)
        return False
    keys = {"mac": mac, "title": title, "dup": dup}
    if info:
        conn.execute(
            f"""
            UPDATE tracks SET artist=:artist, album=:album, genre=:genre,
                artwork_url=:artwork_url, enriched=1
            WHERE {where}
            """,
            {**info, **keys},
        )
    else:
        # Successful lookup, genuinely no match -- done, not a retry case.
        conn.execute(f"UPDATE tracks SET enriched=1 WHERE {where}", keys)
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


def set_favorite(conn: sqlite3.Connection, title: str, artist: str | None, value: bool) -> int:
    """Marks or unmarks a song as a favorite. The Now Playing overlay only
    knows the title and artist: with repeated titles, the copies whose
    artist matches are marked, else every copy. Returns rows changed."""
    rows = conn.execute("SELECT phone_mac, dup, artist FROM tracks WHERE title = ?", (title,)).fetchall()
    matching = [r for r in rows if artist and r[2] and r[2].casefold() == artist.casefold()] or rows
    conn.executemany(
        "UPDATE tracks SET favorite = ? WHERE phone_mac = ? AND title = ? AND dup = ?",
        [(1 if value else 0, mac, title, dup) for mac, dup, _ in matching],
    )
    conn.commit()
    return len(matching)


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
        await bus.subscribe("jukebox/jb-source-phone/forgotten")
        await bus.subscribe("jukebox/jb-source-phone/now-playing")
        await bus.subscribe(f"jukebox/{SERVICE_NAME}/cmd")
        await bus.subscribe("jukebox/jb-crawl/result")
        # USB drives: their songs, and what they're playing (for play counts).
        await bus.subscribe("jukebox/jb-source-local/library")
        await bus.subscribe("jukebox/jb-source-local/now-playing")
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
        drive_plays = PlayTracker(conn, log)   # its mac follows the song playing

        async for topic, payload in messages:
            if topic.endswith("/cmd"):
                if payload.get("action") == "favorite" and payload.get("title"):
                    n = set_favorite(conn, payload["title"], payload.get("artist"), bool(payload.get("value")))
                    log.info("favorite %s: %s (%d rows)", "on" if payload.get("value") else "off", payload["title"], n)
                    await snapshots.publish()
                continue
            if topic.endswith("/connected"):
                plays.on_connected(payload)
                if payload.get("connected") and payload.get("mac") and payload["mac"] != snapshots.mac:
                    snapshots.mac = payload["mac"]
                    log.info("showing the library of %s", payload.get("name") or snapshots.mac)
                    await snapshots.publish()
                continue
            if topic.endswith("/forgotten"):
                mac = payload.get("mac")
                if mac:
                    n = forget_phone(conn, mac)
                    log.info("forgot phone %s: removed %d stored rows", mac, n)
                    if snapshots.mac == mac:
                        snapshots.mac = latest_phone(conn)
                    await snapshots.publish()
                continue
            if topic == "jukebox/jb-source-local/library":
                attached = []
                for drive in payload.get("drives") or []:
                    if not drive.get("id"):
                        continue
                    if drive.get("scanning"):
                        attached.append(f"drive:{drive['id']}")   # what it had, until the scan ends
                        continue
                    songs = [t for t in payload.get("tracks") or []
                             if t.get("title") and str(t.get("item_path", "")).startswith(f"drive:{drive['id']}/")]
                    removed = store_drive(conn, drive["id"], songs, log)
                    attached.append(f"drive:{drive['id']}")
                    log.info("drive %s: %d songs (removed %d no longer on it)", drive["id"], len(songs), removed)
                if attached != snapshots.drives:
                    log.info("drives shown: %s", ", ".join(attached) or "none")
                snapshots.drives = attached
                await snapshots.publish()
                wake.set()   # covers for new songs
                continue
            if topic == "jukebox/jb-source-local/now-playing":
                item_path = str(payload.get("item_path") or "")
                if item_path.startswith("drive:"):
                    drive_plays.mac = "drive:" + item_path[len("drive:"):].partition("/")[0]
                if drive_plays.on_now_playing(payload):
                    await snapshots.publish()
                continue
            if topic.endswith("/now-playing"):
                if plays.on_now_playing(payload):
                    await snapshots.publish()  # once per counted play
                continue
            if topic == "jukebox/jb-crawl/result":
                # One branch of the deep crawl. Confirm it either way, so
                # jb-crawl only counts the branch done once it's stored.
                ack = {k: payload.get(k) for k in ("mac", "branch", "cycle")}
                try:
                    n = store_deep(conn, payload["mac"], payload["branch"], payload["data"])
                    log.info("stored deep crawl %s for %s (%d changes)", payload["branch"], payload["mac"], n)
                    await bus.publish(f"jukebox/{SERVICE_NAME}/deep-ack", {**ack, "ok": True})
                    await snapshots.publish()
                    wake.set()   # re-look-up covers for songs whose real artist is now known
                except Exception:
                    log.exception("couldn't store deep crawl %s", payload.get("branch"))
                    await bus.publish(f"jukebox/{SERVICE_NAME}/deep-ack", {**ack, "ok": False})
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
