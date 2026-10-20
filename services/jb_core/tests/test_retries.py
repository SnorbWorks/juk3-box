import logging

from services.jb_core.main import Queue, drop_retries


def test_a_new_pick_drops_a_song_waiting_to_retry():
    q = Queue()
    q.manual = [{"title": "Take My Breath", "retry": True}, {"title": "Colours"}]
    drop_retries(q, logging.getLogger("test"))
    assert [t["title"] for t in q.manual] == ["Colours"]


def test_phone_songs_wait_while_the_song_list_is_read():
    q = Queue()
    q.phone_connected = True
    q.manual = [{"title": "Dissolve", "item_path": "/org/bluez/x/item1"},
                {"title": "Drive song", "item_path": "drive:abc/song.mp3"}]
    for state in ("connecting", "syncing", "wrong_app"):
        q.phone_sync = state
        assert not q.phone_ready
    q.phone_sync = "syncing"
    assert q.pop_next()["title"] == "Drive song"         # a drive song doesn't wait
    assert q.pop_next() is None                          # the phone's keeps its place
    q.phone_sync = "ready"
    assert q.pop_next()["title"] == "Dissolve"


def test_no_phone_is_never_ready_and_audio_only_tries_anyway():
    q = Queue()
    q.phone_sync = "ready"
    assert not q.phone_ready
    q.phone_connected, q.phone_sync = True, "none"       # audio only: the source reconnects for it
    assert q.phone_ready
