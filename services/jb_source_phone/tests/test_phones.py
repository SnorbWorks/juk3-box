from services.jb_source_phone import phones as ph

A, B, C = "AA:00:00:00:00:01", "BB:00:00:00:00:02", "CC:00:00:00:00:03"


def test_first_phone_gets_the_jukebox_and_others_are_turned_away():
    lock = ph.Lock()
    assert lock.update([A], 0) == []
    assert lock.owner == A
    assert lock.update([A, B], 1) == [B]
    assert lock.owner == A


def test_several_at_once_most_recently_used_wins():
    lock = ph.Lock()
    assert lock.update([A, B], 0, recent_first=[B, A]) == [A]
    assert lock.owner == B


def test_a_drop_mid_song_holds_the_place():
    lock = ph.Lock()
    lock.update([A], 0)
    lock.dropped(A, 10, hold_s=180)
    assert lock.update([B], 20) == [B]          # a guest can't slip in
    assert lock.update([A], 30) == []           # the owner comes back
    assert lock.owner == A and lock.held_until == 0


def test_the_hold_runs_out():
    lock = ph.Lock()
    lock.update([A], 0)
    lock.dropped(A, 10, hold_s=180)
    assert lock.update([B], 200) == []
    assert lock.owner == B


def test_a_drop_while_idle_frees_it():
    lock = ph.Lock()
    lock.update([A], 0)
    lock.dropped(A, 10, hold_s=None)
    assert lock.update([B], 11) == []
    assert lock.owner == B


def test_switching_on_purpose_lets_go_of_the_old_phone():
    lock = ph.Lock()
    lock.update([A], 0)
    lock.switch_to(B, 10)
    assert lock.update([A], 11) == [A]          # old one goes, new one is coming
    assert lock.update([B], 15) == []
    assert lock.owner == B


def test_forgetting_or_disconnecting_the_owner_opens_it():
    lock = ph.Lock()
    lock.update([A], 0)
    lock.forget(A)
    assert lock.owner is None
    lock.update([B], 1)
    lock.release()
    assert lock.update([C], 2) == [] and lock.owner == C


def test_phone_list_puts_the_connected_phone_first():
    paired = [(A, "/a", "Ann's iPhone", False), (B, "/b", "Bo's Pixel", True), (C, "/c", "Cat", False)]
    book = {A: {"last_connected": 300}, C: {"last_connected": 100}}
    names = [p["name"] for p in ph.phone_list(paired, book, B)]
    assert names == ["Bo's Pixel", "Ann's iPhone", "Cat"]
    assert ph.phone_list(paired, book, B)[0]["active"] is True
