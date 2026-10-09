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
