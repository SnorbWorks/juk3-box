import sqlite3

import pytest

from services.jb_library import main as lib

MAC = "88:51:F2:56:4B:2B"


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
