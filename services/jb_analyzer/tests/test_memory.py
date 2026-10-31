import numpy as np

from services.jb_analyzer.memory import Recording, Store, load_arrays, song_key


def test_the_same_song_gets_the_same_key():
    a = song_key("Empire Of The Sun", "Walking On a Dream", 198.4)
    assert a == song_key("  empire of the sun ", "walking on a  dream", 199.0)
    assert a != song_key("Empire Of The Sun", "Walking On a Dream", 230.0)
    assert song_key("x", None) is None


def recording(start_pos=0.0, seconds=100.0, seek=None):
    """A recording ten texture frames a second, the source telling its position every 2 s."""
    rec = Recording({"title": "t", "artist": "a", "duration": 100.0}, 30.0, 0.0)
    f = {"_flux": 0.1, "kick": 0.0, "snare": 0.0, "_rms_db": -20.0, "onset": 0.0, "hat": 0.0, "crash": 0.0,
         "beat": False, "ring": 0.0, "level": 0.5, "brightness": 0.5, "_bass_db": -30.0}
    tex = {"chroma": np.ones(12) / 12, "mfcc": np.zeros(13), "width": 0.2, "centre": 0.5}
    pos = start_pos
    for k in range(int(seconds * 30)):
        t = k / 30
        if seek and abs(t - seek[0]) < 1e-9:
            pos = seek[1] - t
        rec.frame(t, f, True)
        if k % 3 == 2:
            rec.texture(t, tex, -20.0)
        if k % 60 == 0:
            rec.position(t, t + (pos if not seek or t >= seek[0] else start_pos))
    return rec


def test_a_whole_play_covers_the_song():
    assert recording().coverage() > 0.95


def test_a_play_that_started_late_is_not_worth_studying():
    assert recording(start_pos=30.0, seconds=70.0).coverage() == 0.0


def test_a_seek_splits_it_and_the_longest_stretch_counts():
    rec = recording(seek=(40.0, 80.0))                     # at 40 s on the clock, it jumped to 80 s in
    off, start, end = rec.offset()
    assert abs(off - 40) < 1e-6 and start >= 40           # the 60 s after the seek
    assert rec.coverage() == 0.0                           # which didn't start near the song's start


def test_the_store_keeps_maps_and_recordings(tmp_path):
    store = Store(tmp_path)
    key = song_key("a", "t", 100)
    store.save_map(key, {"format": 1, "song": {"title": "t"}, "plays": 1, "learned": "2026-10-30T10:00:00",
                         "sections": [{"role": "intro"}]})
    store.save_recording(key, recording().arrays())
    assert store.load_map(key)["plays"] == 1
    assert load_arrays(store.recording_path(key))["pos"].size > 900
    assert [s["key"] for s in store.songs()] == [key]
    store.forget(key)
    assert store.load_map(key) is None and store.songs() == []
