from datetime import datetime

from services.jb_crawl import policy

MAC = "88:51:F2:56:4B:2B"


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()


def test_first_connect_makes_a_crawl_due():
    st = {}
    assert policy.on_connect(st, MAC, ts("2026-10-11 09:00"))
    assert policy.pending(st, MAC) == list(policy.BRANCHES)


def test_same_day_reconnect_does_not_start_another_cycle():
    st = {}
    policy.on_connect(st, MAC, ts("2026-10-11 09:00"))
    for b in policy.BRANCHES:
        policy.branch_done(st, MAC, b, ts("2026-10-11 09:30"))
    assert not policy.on_connect(st, MAC, ts("2026-10-11 18:00"))
    assert policy.pending(st, MAC) == []


def test_next_day_connect_starts_a_new_cycle():
    st = {}
    policy.on_connect(st, MAC, ts("2026-10-11 09:00"))
    for b in policy.BRANCHES:
        policy.branch_done(st, MAC, b, ts("2026-10-11 09:30"))
    assert policy.on_connect(st, MAC, ts("2026-10-12 08:00"))
    assert policy.pending(st, MAC) == list(policy.BRANCHES)


def test_interrupted_cycle_resumes_unfinished_branches_even_after_reconnects():
    st = {}
    policy.on_connect(st, MAC, ts("2026-10-11 09:00"))
    policy.branch_done(st, MAC, "playlists", ts("2026-10-11 09:10"))
    policy.on_connect(st, MAC, ts("2026-10-11 12:00"))      # same day
    assert policy.pending(st, MAC) == ["artists", "albums", "genres"]
    policy.on_connect(st, MAC, ts("2026-10-12 08:00"))      # next day: still finishing
    assert policy.pending(st, MAC) == ["artists", "albums", "genres"]


def test_cycle_completes_when_every_branch_is_done():
    st = {}
    policy.on_connect(st, MAC, ts("2026-10-11 09:00"))
    done = [policy.branch_done(st, MAC, b, ts("2026-10-11 09:30")) for b in policy.BRANCHES]
    assert done == [False, False, False, True]
    assert st[MAC]["last_complete"] == ts("2026-10-11 09:30")


def test_only_the_last_connect_is_kept():
    st = {}
    for h in range(8, 20):
        policy.on_connect(st, MAC, ts(f"2026-10-11 {h:02d}:00"))
    assert st[MAC]["last_connect"] == ts("2026-10-11 19:00")
    assert len(st[MAC]) == 4


def test_enabled_subset():
    st = {}
    policy.on_connect(st, MAC, ts("2026-10-11 09:00"))
    enabled = ("playlists", "artists")
    assert policy.pending(st, MAC, enabled) == ["playlists", "artists"]
    policy.branch_done(st, MAC, "playlists", 1, enabled)
    assert policy.branch_done(st, MAC, "artists", 2, enabled)


def test_idle_rules():
    now = 10_000
    assert policy.is_idle(now, None, False, None, 300, 120) == (False, "no phone connected")
    assert policy.is_idle(now, None, True, 0, 300, 120)[0] is False
    assert policy.is_idle(now, None, False, now - 60, 300, 120) == (False, "phone just connected")
    assert policy.is_idle(now, now - 100, False, 0, 300, 120) == (False, "jukebox in use")
    assert policy.is_idle(now, now - 400, False, 0, 300, 120) == (True, "idle")


def test_state_round_trip(tmp_path):
    st = {}
    policy.on_connect(st, MAC, ts("2026-10-11 09:00"))
    path = tmp_path / "crawl-state.json"
    policy.save(st, path)
    assert policy.load(path) == st
    assert policy.load(tmp_path / "missing.json") == {}
