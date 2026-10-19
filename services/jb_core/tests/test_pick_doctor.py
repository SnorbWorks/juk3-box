from services.jb_core.pick_doctor import RETRY_GRACE_S, SOUND_S, START_S, STUCK_AFTER, PickDoctor

SONG = {"title": "Bad Dreams", "artist": "Teddy Swims", "item_path": "/x/1"}


def doctor(**phone):
    d = PickDoctor()
    d.phone = {"connected": True, "name": "iPhone", **phone}
    d.audio = {"volume": 0.3, "muted": False}
    d.picked(SONG, 0.0)
    return d


def test_a_pick_heard_playing_is_fine():
    d = doctor()
    d.heard("Bad Dreams", "playing", 2.0)
    d.sound(2.5)
    assert d.check(3.0) is None
    assert d.pick is None
    assert d.check(100.0) is None


def test_no_verdict_before_the_deadline():
    d = doctor()
    assert d.check(START_S - 1) is None


def test_a_phone_with_no_controls():
    d = doctor(controls=False)
    v = d.check(START_S + 1)
    assert v["problem"] == "no_controls"
    assert "Bluetooth" in v["advice"]
    assert v["retry"] and v["track"] == SONG
    assert d.log[0]["title"] == "Bad Dreams" and "track" not in d.log[0]
    assert d.check(START_S + 2) is None               # said once


def test_the_phone_not_answering():
    d = doctor()
    d.source_says({"title": "Bad Dreams", "reason": "no_answer"}, 1.0)
    assert d.check(START_S + 1)["problem"] == "no_answer"


def test_retrying_buys_time_and_says_so_once():
    d = doctor()
    note = d.source_says({"title": "Bad Dreams", "reason": "no_answer", "retrying": True}, 5.0)
    assert note["problem"] == "retrying"
    assert d.source_says({"title": "Bad Dreams", "retrying": True}, 6.0) is None
    assert d.check(START_S + 1) is None
    d.heard("Bad Dreams", "playing", 40.0)
    d.sound(41.0)
    assert d.check(42.0) is None and d.pick is None


def test_retrying_runs_out():
    d = doctor()
    d.source_says({"title": "Bad Dreams", "reason": "no_answer", "retrying": True}, 5.0)
    assert d.check(5.0 + RETRY_GRACE_S + 1)["problem"] == "no_answer"


def test_a_report_about_another_song_is_ignored():
    d = doctor()
    d.source_says({"title": "Something Else", "reason": "not_found"}, 1.0)
    assert d.check(START_S + 1)["problem"] == "no_start"


def test_play_error_diagnoses_at_once():
    d = doctor()
    v = d.failed({"title": "Bad Dreams", "reason": "not_found"}, 2.0)
    assert v["problem"] == "not_found" and not v["retry"]


def test_another_song_playing_is_not_the_pick():
    d = doctor()
    d.heard("Something Else", "playing", 2.0)
    d.sound(2.5)
    assert d.check(START_S + 1)["problem"] == "no_start"


def test_playing_but_muted():
    d = doctor()
    d.audio = {"volume": 0.3, "muted": True}
    d.heard("Bad Dreams", "playing", 2.0)
    assert d.check(2.0 + SOUND_S - 1) is None
    assert d.check(2.0 + SOUND_S + 1)["problem"] == "muted"


def test_playing_somewhere_else():
    d = doctor()
    d.sound(1.0)                                       # the song before, not this one
    d.heard("Bad Dreams", "playing", 2.0)
    v = d.check(2.0 + SOUND_S + 1)
    assert v["problem"] == "no_sound" and not v["retry"]


def test_forget():
    d = doctor()
    d.forget()
    assert d.check(START_S + 1) is None


def test_picks_failing_in_a_row_get_stuck_and_a_play_clears_it():
    d = PickDoctor()
    for k in range(STUCK_AFTER - 1):
        d.picked(SONG, k)
        d.failed({"reason": "not_found"}, k)
    assert not d.stuck
    d.heard("Anything", "playing", 10.0)
    assert d.in_row == 0
    for k in range(STUCK_AFTER):
        d.picked(SONG, k)
        d.failed({"reason": "not_found"}, k)
    assert d.stuck
    v = d.stuck_verdict()
    assert v["problem"] == "stuck" and "Shuffle is off" in v["advice"]
    assert not d.stuck and d.log[0]["problem"] == "stuck"
