"""The song now playing: which one (the sources' word), learning it after a
whole play, and following its map when it comes round again."""

from services.jb_analyzer import study
from services.jb_analyzer.songs import Songs
from services.jb_analyzer.tests import songmaker as sm

PHONE = "jukebox/jb-source-phone/now-playing"
USB = "jukebox/jb-source-local/now-playing"


def said(title="made up", status="playing", pos=None, duration=None):
    """A source's now-playing message."""
    msg = {"title": title, "artist": "the tests", "album": None, "genre": None, "status": status}
    if duration:
        msg["duration_ms"] = int(duration * 1000)
    if pos is not None:
        msg["position_ms"] = int(pos * 1000)
    return msg


def play(songs, x, t0, start_s=0.0, seconds=None, source_off=0.0):
    """The samples played from start_s, from t0 on the clock, the phone saying
    where it is every 2 s (source_off out) -> (the clock at the end, section
    events as (t, the true song position, event))."""
    dur = x.shape[0] / sm.RATE
    s, k, out, t = int(start_s * sm.RATE), 0, [], t0
    end = x.shape[0] if seconds is None else min(x.shape[0], s + int(seconds * sm.RATE))
    while s + 1600 <= end:
        t = t0 + (k + 1) * 1600 / sm.RATE
        true = (s + 1600) / sm.RATE
        if k % 60 == 0:
            songs.now_playing(t, PHONE, said(pos=true + source_off, duration=dur))
        _, events = songs.frame(t, x[s:s + 1600])
        out += [(t, true, e) for e in events if e["type"] == "section"]
        s += 1600
        k += 1
    return t, out


def test_only_a_source_that_is_playing_says_which_song_it_is():
    songs = Songs(sm.RATE, 1600)
    assert not songs.now_playing(0.0, PHONE, said(status="paused"))["changed"]
    assert songs.key is None
    assert songs.now_playing(1.0, PHONE, said())["changed"] and songs.key
    key = songs.key
    assert not songs.now_playing(2.0, USB, said("an old one", status="paused"))["changed"]
    assert songs.key == key                                    # a paused drive's song isn't the song
    assert songs.now_playing(3.0, USB, said("the next one"))["changed"]
    assert songs.identity["title"] == "the next one" and songs.key != key
    # a phone that never says whether it's playing, naming a song: it is
    assert songs.now_playing(4.0, PHONE, said("a third", status=None))["changed"]


def test_a_whole_play_is_worth_studying_and_half_of_one_is_not():
    x, _ = sm.song(sm.PLAN)
    dur = x.shape[0] / sm.RATE
    songs = Songs(sm.RATE, 1600)
    t, events = play(songs, x, 100.0)
    live = [e for _, _, e in events if not e.get("update")]
    assert len(live) >= 4 and all(e["source"] == "live" for e in live)
    for e in live:                                             # on the clock, and in the song
        assert abs(e["start"] - (e["at"] - 100.0)) < 0.05
    m = songs.map_payload()
    assert not m["known"] and len(m["sections"]) == len(live)
    assert set(songs.slow()) >= {"song", "known", "key", "ears", "voice", "feel"}
    assert songs.tick(t + 1.0) is None
    done = songs.tick(t + 25.0)                                # quiet that long: over
    assert done["coverage"] > 0.95 and done["arrays"] is not None and not done["known"]

    songs.now_playing(t + 30, PHONE, said(pos=0.0, duration=dur))
    t2, _ = play(songs, x, t + 30, seconds=dur / 2)
    r = songs.now_playing(t2 + 0.1, PHONE, said("another one", pos=0.0, duration=200.0))
    assert r["changed"] and r["finished"]["arrays"] is None    # half a play: not worth it


def test_a_known_song_follows_its_map_on_time():
    _, _, rec, x = sm.heard()                                  # its first play
    m = study.study(rec)
    songs = Songs(sm.RATE, 1600)
    t0 = 1000.0
    songs.now_playing(t0, PHONE, said(pos=30.0 + 1.2, duration=x.shape[0] / sm.RATE))   # 1.2 s out
    songs.learned(songs.key, m, rec)
    assert songs.slow()["known"] and songs.map_payload()["known"]
    _, events = play(songs, x, t0, start_s=30.0, source_off=1.2)
    first = next(i for i, (_, _, e) in enumerate(events) if e["source"] == "map")
    assert all(e["source"] == "map" for _, _, e in events[first:])   # the map, once it's found its place
    starts = sm.section_starts(sm.PLAN)
    mapped = events[first + 1:]                                # (the first: the section it found itself in)
    assert len(mapped) >= 2
    for t, true, e in mapped:
        want = min(starts, key=lambda s: abs(s - e["start"]))
        assert abs(e["start"] - want) < 0.15                   # where in the song
        assert abs(e["at"] - (t - (true - want))) < 0.15       # when, on the clock
        assert t - e["at"] < 0.1                               # and told then


def test_a_known_song_with_no_recording_follows_its_map_by_the_source():
    _, _, rec, x = sm.heard()
    m = study.study(rec)
    songs = Songs(sm.RATE, 1600)
    songs.now_playing(0.0, PHONE, said(pos=40.0, duration=x.shape[0] / sm.RATE))
    songs.learned(songs.key, m, None)
    _, events = play(songs, x, 0.0, start_s=40.0, seconds=20.0)
    assert events and all(e["source"] == "map" for _, _, e in events)
