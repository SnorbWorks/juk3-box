import sqlite3

import pytest

from services.jb_library import main as lib

MAC = "AA:BB:CC:12:34:56"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "DB_PATH", str(tmp_path / "library.db"))
    return lib.get_db()


def crawl(*titles):
    return [{"title": t, "item_path": f"/p/item{i}"} for i, t in enumerate(titles)]


def rows(db):
    return db.execute("SELECT title, dup, item_path FROM tracks ORDER BY title, dup").fetchall()


def test_duplicate_titles_are_kept_as_separate_copies(db):
    lib.upsert_tracks(db, MAC, crawl("Animal", "Animal", "Sweater Weather"))
    assert rows(db) == [("Animal", 0, "/p/item0"), ("Animal", 1, "/p/item1"),
                        ("Sweater Weather", 0, "/p/item2")]


def test_songs_no_longer_on_the_phone_are_removed(db):
    lib.upsert_tracks(db, MAC, crawl("A", "B", "C", "D"))
    removed = lib.upsert_tracks(db, MAC, crawl("A", "B", "D"))
    assert removed == 1
    assert [r[0] for r in rows(db)] == ["A", "B", "D"]


def test_a_resync_updates_play_paths_and_keeps_enrichment_and_counts(db):
    lib.upsert_tracks(db, MAC, crawl("A", "B"))
    db.execute("UPDATE tracks SET artist='X', play_count=3 WHERE title='A'")
    lib.upsert_tracks(db, MAC, [{"title": "A", "item_path": "/new/a"}, {"title": "B", "item_path": "/new/b"}])
    assert db.execute("SELECT item_path, artist, play_count FROM tracks WHERE title='A'").fetchone() == ("/new/a", "X", 3)


def test_a_suspiciously_short_sync_removes_nothing(db):
    lib.upsert_tracks(db, MAC, crawl(*[f"Song {i}" for i in range(10)]))
    removed = lib.upsert_tracks(db, MAC, crawl("Song 0", "Song 1", "Song 2"))  # 3 of 10
    assert removed == 0
    assert len(rows(db)) == 10


def test_old_title_keyed_database_is_migrated_with_data_kept(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE tracks (phone_mac TEXT NOT NULL, title TEXT NOT NULL, item_path TEXT,
        artist TEXT, album TEXT, genre TEXT, artwork_url TEXT, enriched INTEGER DEFAULT 0,
        play_count INTEGER DEFAULT 0, last_played_at REAL, lastfm_listeners INTEGER,
        popularity_checked INTEGER DEFAULT 0, PRIMARY KEY (phone_mac, title))""")
    old.execute("INSERT INTO tracks (phone_mac, title, item_path, artist, play_count, lastfm_listeners) "
                "VALUES (?, 'Animal', '/p/1', 'KATSEYE', 4, 1234)", (MAC,))
    old.commit()
    old.close()
    monkeypatch.setattr(lib, "DB_PATH", str(path))
    db = lib.get_db()
    assert db.execute("SELECT title, dup, artist, play_count, lastfm_listeners FROM tracks").fetchall() == [
        ("Animal", 0, "KATSEYE", 4, 1234)]
    lib.upsert_tracks(db, MAC, crawl("Animal", "Animal"))  # the second copy now fits
    assert [(r[0], r[1]) for r in rows(db)] == [("Animal", 0), ("Animal", 1)]


def test_play_count_goes_to_the_copy_whose_artist_matches(db):
    lib.upsert_tracks(db, MAC, crawl("Animal", "Animal"))
    db.execute("UPDATE tracks SET artist = CASE dup WHEN 0 THEN 'KATSEYE' ELSE 'Neon Trees' END")
    tracker = lib.PlayTracker(db, log=type("L", (), {"info": lambda *a: None})())
    tracker.on_connected({"connected": True, "mac": MAC})
    tracker.on_now_playing({"title": "Animal", "artist": "neon trees", "status": "playing", "position_ms": 1000})
    assert tracker.on_now_playing({"title": "Animal", "artist": "neon trees", "status": "playing",
                                   "position_ms": 31_000}) is True
    assert db.execute("SELECT dup, play_count FROM tracks ORDER BY dup").fetchall() == [(0, 0), (1, 1)]


def added(db):
    return dict(db.execute("SELECT title, added_at FROM tracks").fetchall())


def test_songs_added_after_the_first_sync_are_stamped(db):
    lib.upsert_tracks(db, MAC, crawl("Animal", "Sweater Weather"), now=100.0)
    assert added(db) == {"Animal": None, "Sweater Weather": None}  # there from the start
    lib.upsert_tracks(db, MAC, crawl("Animal", "Sweater Weather", "Dreams"), now=200.0)
    assert added(db) == {"Animal": None, "Sweater Weather": None, "Dreams": 200.0}
    lib.upsert_tracks(db, MAC, crawl("Animal", "Sweater Weather", "Dreams"), now=300.0)
    assert added(db)["Dreams"] == 200.0  # a resync doesn't make it new again


def favorites(db):
    return db.execute("SELECT title, dup, favorite FROM tracks ORDER BY title, dup").fetchall()


def test_favorite_marks_the_copy_whose_artist_matches(db):
    lib.upsert_tracks(db, MAC, crawl("Animal", "Animal", "Dreams"))
    db.execute("UPDATE tracks SET artist = 'KATSEYE' WHERE title = 'Animal' AND dup = 1")
    assert lib.set_favorite(db, "Animal", "katseye", True) == 1
    assert favorites(db) == [("Animal", 0, 0), ("Animal", 1, 1), ("Dreams", 0, 0)]
    lib.set_favorite(db, "Dreams", None, True)   # no artist known: every copy
    lib.set_favorite(db, "Animal", "KATSEYE", False)
    assert favorites(db) == [("Animal", 0, 0), ("Animal", 1, 0), ("Dreams", 0, 1)]


# -- deep crawl storage (jb-crawl results) ---------------------------------

def test_deep_playlists_replace_the_previous_set(db):
    lib.upsert_tracks(db, MAC, crawl("A", "B", "C"))
    lib.store_deep(db, MAC, "playlists", {"playlists": {"Mix": ["B", "A"], "Old": ["C"]}})
    lib.store_deep(db, MAC, "playlists", {"playlists": {"Mix": ["C", "B", "A"]}})
    assert lib.read_playlists(db) == [{"name": "Mix", "titles": ["C", "B", "A"]}]


def test_deep_artists_beat_itunes_and_trigger_a_new_cover_lookup(db):
    lib.upsert_tracks(db, MAC, crawl("Flaws", "Cry"))
    db.execute("UPDATE tracks SET artist='Calum Scott', enriched=1, popularity_checked=1")
    lib.store_deep(db, MAC, "artists", {"artists": {"Bastille": ["Flaws"]}})
    row = db.execute("SELECT COALESCE(phone_artist, artist), enriched, popularity_checked"
                     " FROM tracks WHERE title='Flaws'").fetchone()
    assert row == ("Bastille", 0, 0)
    untouched = db.execute("SELECT phone_artist, enriched FROM tracks WHERE title='Cry'").fetchone()
    assert untouched == (None, 1)
    # Storing the same answer again changes nothing and re-triggers nothing.
    db.execute("UPDATE tracks SET enriched=1")
    assert lib.store_deep(db, MAC, "artists", {"artists": {"Bastille": ["Flaws"]}}) == 0
    assert db.execute("SELECT enriched FROM tracks WHERE title='Flaws'").fetchone() == (1,)


def test_deep_artists_split_duplicate_titles(db):
    lib.upsert_tracks(db, MAC, crawl("Home", "Home"))
    lib.store_deep(db, MAC, "artists", {"artists": {"Edward Sharpe": ["Home"], "Phillip Phillips": ["Home"]}})
    got = db.execute("SELECT dup, phone_artist FROM tracks ORDER BY dup").fetchall()
    assert got == [(0, "Edward Sharpe"), (1, "Phillip Phillips")]


def test_deep_albums_track_numbers_and_recent_order(db):
    lib.upsert_tracks(db, MAC, crawl("One", "Two", "Solo"))
    lib.store_deep(db, MAC, "albums", {"albums": {"Pair": ["One", "Two"], "Single": ["Solo"]},
                                       "recent": ["Single", "Pair"]})
    got = db.execute("SELECT title, phone_album, album_track FROM tracks ORDER BY title").fetchall()
    assert got == [("One", "Pair", 1), ("Solo", "Single", 1), ("Two", "Pair", 2)]
    assert db.execute("SELECT album, rank FROM album_recent ORDER BY rank").fetchall() == [("Single", 0), ("Pair", 1)]


def test_deep_genres_come_through_the_artist(db):
    lib.upsert_tracks(db, MAC, crawl("Flaws", "Cry"))
    lib.store_deep(db, MAC, "artists", {"artists": {"Bastille": ["Flaws"]}})
    db.execute("UPDATE tracks SET artist='Cigarettes After Sex' WHERE title='Cry'")   # iTunes only
    lib.store_deep(db, MAC, "genres", {"genres": {"Alternative": ["bastille", "Cigarettes After Sex"],
                                                  "Pop": ["Bastille"]}})
    got = db.execute("SELECT title, phone_genre FROM tracks ORDER BY title").fetchall()
    assert got == [("Cry", "Alternative"), ("Flaws", "Alternative")]


def test_deep_unknown_branch_is_rejected(db):
    with pytest.raises(ValueError):
        lib.store_deep(db, MAC, "podcasts", {})


def test_album_index_lists_every_album_with_its_recent_rank(db):
    lib.store_deep(db, MAC, "album_index", {"albums": ["Abbey Road", "Dawn FM", "Mezzanine"],
                                            "recent": ["Dawn FM", "Abbey Road"]})
    got = lib.read_albums(db)
    assert got["albums"] == [{"name": "Abbey Road", "title": "Abbey Road", "nth": 0, "rank": 1},
                             {"name": "Dawn FM", "title": "Dawn FM", "nth": 0, "rank": 0},
                             {"name": "Mezzanine", "title": "Mezzanine", "nth": 0, "rank": None}]
    assert got["contents"] == {}


def test_opened_album_matches_songs_by_phone_id_not_title(db):
    # Two songs called "Home"; the album holds the second (by its phone ID).
    lib.upsert_tracks(db, MAC, [{"title": "Home", "item_path": "/p/Songs/item111"},
                                {"title": "Home", "item_path": "/p/Songs/item222"},
                                {"title": "Intro", "item_path": "/p/Songs/item333"}])
    lib.store_deep(db, MAC, "album", {"name": "Live", "tracks": [{"title": "Intro", "uid": "333"},
                                                                 {"title": "Home", "uid": "222"}]})
    got = db.execute("SELECT title, dup, phone_album, album_track FROM tracks ORDER BY title, dup").fetchall()
    assert got == [("Home", 0, None, None), ("Home", 1, "Live", 2), ("Intro", 0, "Live", 1)]
    assert lib.read_albums(db)["contents"] == {"Live": [{"title": "Intro", "uid": "333"},
                                                         {"title": "Home", "uid": "222"}]}


def test_albums_sharing_a_name_stay_apart(db):
    lib.store_deep(db, MAC, "album_index", {"albums": ["Greatest Hits", "Abbey Road", "Greatest Hits"], "recent": []})
    got = [(a["name"], a["title"], a["nth"]) for a in lib.read_albums(db)["albums"]]
    assert got == [("Greatest Hits", "Greatest Hits", 0), ("Abbey Road", "Abbey Road", 0),
                   ("Greatest Hits (2)", "Greatest Hits", 1)]


def test_old_name_keyed_album_index_is_rebuilt(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE album_index (phone_mac TEXT, name TEXT, position INTEGER, PRIMARY KEY (phone_mac, name))")
    old.commit(); old.close()
    monkeypatch.setattr(lib, "DB_PATH", str(path))
    conn = lib.get_db()
    pk = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(album_index)")}
    assert pk["position"] and not pk["name"]


OTHER = "AA:BB:CC:DD:EE:FF"


def test_each_phone_sees_only_its_own_playlists_and_forgetting_removes_everything(db):
    lib.upsert_tracks(db, MAC, crawl("Mine"))
    lib.upsert_tracks(db, OTHER, crawl("Theirs"))
    lib.store_deep(db, MAC, "playlists", {"playlists": {"Road Trip": ["Mine"]}})
    lib.store_deep(db, OTHER, "playlists", {"playlists": {"Gym": ["Theirs"]}})
    assert [p["name"] for p in lib.read_playlists(db, MAC)] == ["Road Trip"]
    assert [p["name"] for p in lib.read_playlists(db, OTHER)] == ["Gym"]
    assert lib.forget_phone(db, OTHER) >= 2
    assert db.execute("SELECT COUNT(*) FROM tracks WHERE phone_mac = ?", (OTHER,)).fetchone()[0] == 0
    assert lib.read_playlists(db, OTHER) == []
    assert [t for (t, _, _) in rows(db)] == ["Mine"]


def test_the_board_falls_back_to_the_phone_last_played(db):
    lib.upsert_tracks(db, MAC, crawl("Mine"))
    lib.upsert_tracks(db, OTHER, crawl("Theirs"))
    db.execute("UPDATE tracks SET last_played_at = 100 WHERE phone_mac = ?", (MAC,))
    db.execute("UPDATE tracks SET last_played_at = 50 WHERE phone_mac = ?", (OTHER,))
    assert lib.latest_phone(db) == MAC
    lib.forget_phone(db, MAC)
    assert lib.latest_phone(db) == OTHER


# -- USB drives (jb-source-local) -----------------------------------------

class FakeBus:
    def __init__(self):
        self.sent = {}

    async def publish(self, topic, payload, retain=False):
        self.sent[topic] = payload


class Log:
    def info(self, *a): pass
    def warning(self, *a): pass


def drive_songs(*titles, drive="6AD0"):
    return [{"title": t, "artist": f"{t} artist", "album": "LP", "genre": "Pop", "album_track": i + 1,
             "item_path": f"drive:{drive}/Music/{t}.mp3"} for i, t in enumerate(titles)]


def test_a_drives_tags_are_stored_as_the_songs_own(db):
    lib.store_drive(db, "6AD0", drive_songs("Tunnel"))
    row = db.execute("SELECT phone_mac, phone_artist, phone_album, phone_genre, album_track, item_path"
                     " FROM tracks").fetchone()
    assert row == ("drive:6AD0", "Tunnel artist", "LP", "Pop", 1, "drive:6AD0/Music/Tunnel.mp3")


def test_a_changed_artist_tag_asks_for_a_new_cover(db):
    lib.store_drive(db, "6AD0", drive_songs("Tunnel"))
    db.execute("UPDATE tracks SET enriched = 1")
    lib.store_drive(db, "6AD0", drive_songs("Tunnel"))
    assert db.execute("SELECT enriched FROM tracks").fetchone()[0] == 1
    changed = drive_songs("Tunnel")
    changed[0]["artist"] = "Someone else"
    lib.store_drive(db, "6AD0", changed)
    assert db.execute("SELECT enriched FROM tracks").fetchone()[0] == 0


def test_drive_songs_are_shown_with_the_phones_while_the_drive_is_in(db):
    import asyncio
    lib.upsert_tracks(db, MAC, crawl("Phone song"))
    lib.upsert_tracks(db, "11:22:33:44:55:66", crawl("Other phone's song"))
    lib.store_drive(db, "6AD0", drive_songs("Drive song"))
    snaps = lib.Snapshots(FakeBus(), db, Log())
    snaps.mac = MAC

    def shown():
        asyncio.run(snaps.publish())
        return [t["title"] for t in snaps.bus.sent["jukebox/jb-library/tracks"]["tracks"]]

    assert shown() == ["Phone song"]                       # drive not plugged in
    snaps.drives = ["drive:6AD0"]
    assert shown() == ["Drive song", "Phone song"]          # merged, not either/or
    snaps.mac = None                                        # no phone ever used
    assert "Drive song" in shown()


def test_the_last_phone_used_is_never_a_drive(db):
    lib.upsert_tracks(db, MAC, crawl("A"))
    lib.store_drive(db, "6AD0", drive_songs("B"))
    db.execute("UPDATE tracks SET last_played_at = 100 WHERE phone_mac = 'drive:6AD0'")
    assert lib.latest_phone(db) == MAC


def test_a_drive_song_with_no_artist_isnt_guessed(db):
    songs = drive_songs("After Hours")
    songs[0]["artist"] = None
    lib.store_drive(db, "6AD0", songs)
    db.execute("UPDATE tracks SET artist = 'The Weeknd', artwork_url = 'x', enriched = 0")  # an earlier guess
    lib.store_drive(db, "6AD0", songs)
    assert db.execute("SELECT artist, artwork_url, enriched, popularity_checked FROM tracks").fetchone() == (
        None, None, 1, 1)


def test_drive_albums_come_with_their_songs_in_order(db):
    db.executemany("INSERT INTO album_index (phone_mac, position, name) VALUES (?, ?, ?)",
                   [(MAC, 0, "Abbey Road"), (MAC, 1, "Thriller")])
    songs = drive_songs("Second", "First")
    songs[0].update(album="Moonlight", album_track=2)
    songs[1].update(album="Moonlight", album_track=1)
    lib.store_drive(db, "6AD0", songs)
    albums = lib.add_drive_albums(db, lib.read_albums(db, MAC), ["drive:6AD0"])
    assert [a["name"] for a in albums["albums"]] == ["Abbey Road", "Moonlight", "Thriller"]
    assert [s["title"] for s in albums["contents"]["Moonlight"]] == ["First", "Second"]
    assert albums["contents"]["Moonlight"][0]["item_path"] == "drive:6AD0/Music/First.mp3"
    assert albums["partial"] == []


def test_an_album_on_the_phone_and_a_drive_is_listed_once(db):
    db.execute("INSERT INTO album_index (phone_mac, position, name) VALUES (?, 0, 'LP')", (MAC,))
    lib.store_drive(db, "6AD0", drive_songs("Track A"))          # album "LP"
    albums = lib.add_drive_albums(db, lib.read_albums(db, MAC), ["drive:6AD0"])
    assert [a["name"] for a in albums["albums"]] == ["LP"]
    assert albums["partial"] == ["LP"]                          # the phone's songs still to fetch
    db.executemany("INSERT INTO album_tracks (phone_mac, album, position, title, uid) VALUES (?, 'LP', ?, ?, ?)",
                   [(MAC, 0, "Track A", "11"), (MAC, 1, "Track B", "12")])
    albums = lib.add_drive_albums(db, lib.read_albums(db, MAC), ["drive:6AD0"])
    assert albums["contents"]["LP"] == [
        {"title": "Track A", "uid": "11", "item_path": "drive:6AD0/Music/Track A.mp3"},   # plays from the drive
        {"title": "Track B", "uid": "12"}]
    assert albums["partial"] == []


def test_edits_win_over_tags_and_undoing_them_keeps_the_play_count(db):
    songs = drive_songs("track01")
    songs[0].update(artist=None, album=None)
    lib.store_drive(db, "6AD0", songs)
    db.execute("UPDATE tracks SET play_count = 4")
    path = songs[0]["item_path"]
    lib.edit_drive_song(db, path, {"title": "Moon River", "artist": "Andy Williams", "album": ""}, "track01")
    lib.store_drive(db, "6AD0", songs)
    assert db.execute("SELECT title, phone_artist, phone_album, play_count FROM tracks").fetchone() == (
        "Moon River", "Andy Williams", None, 4)
    lib.edit_drive_song(db, path, {}, "track01")                 # back to the file's info
    lib.store_drive(db, "6AD0", songs)
    assert db.execute("SELECT title, phone_artist, play_count FROM tracks").fetchone() == ("track01", None, 4)
    assert db.execute("SELECT COUNT(*) FROM drive_edits").fetchone()[0] == 0


def test_a_new_drive_puts_only_its_newest_songs_in_new(db):
    songs = drive_songs(*[f"Song {i:02}" for i in range(30)])
    for i, s in enumerate(songs):
        s["mtime"] = i
    lib.store_drive(db, "6AD0", songs, now=1000)
    stamped = [r[0] for r in db.execute("SELECT title FROM tracks WHERE added_at = 1000 ORDER BY title")]
    assert stamped == [f"Song {i:02}" for i in range(10, 30)]
    assert db.execute("SELECT COUNT(*) FROM tracks WHERE first_seen = 1000").fetchone()[0] == 30
    lib.store_drive(db, "6AD0", songs + drive_songs("Fresh"), now=2000)   # added later: New
    assert db.execute("SELECT added_at FROM tracks WHERE title = 'Fresh'").fetchone()[0] == 2000


def test_the_helper_apps_library_is_shown_with_its_own_tags_covers_albums_and_playlists(db):
    import asyncio
    mac = "helper:dev-1"
    tracks = [
        {"title": "Moon River", "artist": "Andy Williams", "album": "Moon River", "genre": "Pop", "album_track": 1,
         "added_at": 1709294400.0, "artwork_key": "a" * 40, "item_path": f"{mac}/i.1"},
        {"title": "Days of Wine", "artist": "Andy Williams", "album": "Moon River", "album_track": 2,
         "added_at": None, "artwork_key": None, "item_path": f"{mac}/i.2"},
    ]
    lib.upsert_tracks(db, MAC, crawl("Bluetooth song"))
    lib.store_helper(db, mac, tracks, [{"name": "Moons", "titles": ["Moon River"]}])
    snaps = lib.Snapshots(FakeBus(), db, Log())
    snaps.mac = mac
    asyncio.run(snaps.publish())
    shown = {t["title"]: t for t in snaps.bus.sent["jukebox/jb-library/tracks"]["tracks"]}
    assert set(shown) == {"Moon River", "Days of Wine"}           # one phone's songs at a time
    assert shown["Moon River"]["artist"] == "Andy Williams" and shown["Moon River"]["added_at"] == 1709294400.0
    assert shown["Moon River"]["artwork_url"] == f"/covers/{'a' * 40}.jpg"
    assert db.execute("SELECT enriched FROM tracks WHERE title = 'Moon River'").fetchone()[0] == 1
    assert db.execute("SELECT enriched FROM tracks WHERE title = 'Days of Wine'").fetchone()[0] == 0   # iTunes cover
    albums = snaps.bus.sent["jukebox/jb-library/albums"]
    assert [s["title"] for s in albums["contents"]["Moon River"]] == ["Moon River", "Days of Wine"]
    assert snaps.bus.sent["jukebox/jb-library/playlists"]["playlists"] == [{"name": "Moons", "titles": ["Moon River"]}]


def test_bluetooth_songs_borrow_the_helper_apps_exact_tags(db):
    import asyncio
    lib.upsert_tracks(db, MAC, crawl("Moon River", "Reaper", "Reaper", "Nowhere"))
    db.execute("UPDATE tracks SET artist = 'andy williams', album = 'Guess' WHERE title = 'Moon River'")
    db.execute("UPDATE tracks SET artist = 'Blue Oyster Cult', album = 'Guess' WHERE title = 'Reaper' AND dup = 0")
    db.execute("UPDATE tracks SET artist = 'Someone', album = 'Guess' WHERE title = 'Reaper' AND dup = 1")
    db.execute("UPDATE tracks SET artist = 'Nobody', album = 'Guess' WHERE title = 'Nowhere'")
    mac = "helper:dev-1"
    lib.store_helper(db, mac, [
        {"title": "Moon River", "artist": "Andy Williams", "album": "Moon River", "genre": "Pop", "album_track": 1,
         "item_path": f"{mac}/i.1"},
        {"title": "Reaper", "artist": "Blue Oyster Cult", "album": "Agents of Fortune", "album_track": 2,
         "item_path": f"{mac}/i.2"},
        {"title": "Reaper", "artist": "Cover Band", "album": "Covers", "item_path": f"{mac}/i.3"},
    ], [])
    snaps = lib.Snapshots(FakeBus(), db, Log())
    snaps.mac = MAC
    asyncio.run(snaps.publish())
    shown = [(t["title"], t["artist"], t["album"], t["album_track"])
             for t in snaps.bus.sent["jukebox/jb-library/tracks"]["tracks"]]
    assert ("Moon River", "Andy Williams", "Moon River", 1) in shown          # the only copy: borrowed
    assert ("Reaper", "Blue Oyster Cult", "Agents of Fortune", 2) in shown    # the copy whose artist matches
    assert ("Reaper", "Someone", "Guess", None) in shown                      # no match: the guess stays
    assert ("Nowhere", "Nobody", "Guess", None) in shown                      # not in the app
