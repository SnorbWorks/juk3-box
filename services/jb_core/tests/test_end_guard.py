from services.jb_core.end_guard import END_MARGIN_S, EndGuard


def np(title="Song", status="playing", position=0, duration=200_000):
    return {"title": title, "status": status, "position_ms": position, "duration_ms": duration}


def test_stops_the_last_song_just_before_its_end_once():
    g = EndGuard()
    g.update(np(position=190_000), now=100.0)                     # 10 s left
    assert g.due(100.0 + 9.0, queue_empty=True) is None
    assert g.due(100.0 + 10 - END_MARGIN_S, queue_empty=True) == "Song"
    assert g.due(100.0 + 11, queue_empty=True) is None            # only once


def test_later_reports_retime_the_end():
    g = EndGuard()
    g.update(np(position=100_000), now=0.0)
    g.update(np(position=101_000), now=4.0)                       # the phone ran slow: 99 s left from t=4
    assert g.due(99.0, queue_empty=True) is None
    assert g.due(4.0 + 99 - END_MARGIN_S, queue_empty=True) == "Song"


def test_leaves_it_alone_with_something_queued_paused_or_unknown_length():
    g = EndGuard()
    g.update(np(position=199_000), now=0.0)
    assert g.due(5.0, queue_empty=False) is None                  # the queue plays on
    g.update(np(status="paused", position=199_000), now=0.0)
    assert g.due(5.0, queue_empty=True) is None
    g.update(np(duration=None), now=0.0)
    assert g.due(500.0, queue_empty=True) is None
    g.update(np(duration=3_000, position=0), now=0.0)             # junk length
    assert g.due(5.0, queue_empty=True) is None


def test_a_new_song_arms_it_again():
    g = EndGuard()
    g.update(np(position=199_000), now=0.0)
    assert g.due(1.0, queue_empty=True) == "Song"
    g.update(np(title="Next", position=199_000), now=10.0)
    assert g.due(11.0, queue_empty=True) == "Next"


def test_ignores_timing_when_the_reports_stopped_long_ago():
    g = EndGuard()
    g.update(np(position=199_000), now=0.0)
    assert g.due(60.0, queue_empty=True) is None
