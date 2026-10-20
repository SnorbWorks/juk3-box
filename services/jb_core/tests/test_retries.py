import logging

from services.jb_core.main import Queue, drop_retries


def test_a_new_pick_drops_a_song_waiting_to_retry():
    q = Queue()
    q.manual = [{"title": "Take My Breath", "retry": True}, {"title": "Colours"}]
    drop_retries(q, logging.getLogger("test"))
    assert [t["title"] for t in q.manual] == ["Colours"]
