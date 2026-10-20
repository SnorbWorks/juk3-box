import time

from services.jb_lights import accent, matrix
from services.jb_lights.accent_lights import Looks
from services.jb_lights.audio_feed import WAVES_LOOK, AudioFeed, LookPicker, Snapshot
from services.jb_lights.levelbar import MusicLooks
from services.jb_lights.themes.volume_bar_idle import Idle


def play(picker, start, seconds, arousal, step=0.5):
    look, t = None, start
    while t < start + seconds:
        look = picker.update(t, True, arousal)
        t += step
    return look, t


def test_music_starts_lively_or_calm_by_mood():
    assert play(LookPicker("auto"), 0, 1, 0.7)[0] == "eq"
    assert play(LookPicker("auto"), 0, 1, 0.3)[0] == "ribbon"
    assert play(LookPicker("auto"), 0, 1, None)[0] == "eq"


def test_early_in_a_song_the_look_follows_the_settling_mood():
    p = LookPicker("auto")
    _, t = play(p, 0, 3, 0.7)                 # last song's mood still in the window
    look, _ = play(p, t, 6, 0.3)              # ...then this song's reads calm
    assert look == "ribbon"


def test_after_settling_the_look_holds_through_wobbles_and_only_moves_on_a_clear_change():
    p = LookPicker("auto")
    _, t = play(p, 0, 20, 0.6)
    look, t = play(p, t, 40, 0.45)            # between the thresholds: stays
    assert look == "eq"
    look, t = play(p, t, 5, 0.3)              # clearly calmer: changes
    assert look == "ribbon"
    look, t = play(p, t, 10, 0.7)             # but not again within the dwell time
    assert look == "ribbon"
    look, t = play(p, t, 30, 0.7)
    assert look == "eq"


def test_idle_rides_over_gaps_between_songs():
    p = LookPicker("auto")
    _, t = play(p, 0, 20, 0.7)
    assert p.update(t + 2, False, 0.7) == "eq"
    assert p.update(t + 5, False, 0.7) == WAVES_LOOK          # idle starts on the waves


def test_pinned_look():
    assert play(LookPicker("ribbon"), 0, 1, 0.9)[0] == "ribbon"


def test_feed_turns_the_stream_into_snapshots():
    feed = AudioFeed(picker=LookPicker("auto"))
    feed.on_message("jukebox/analysis/audio/slow", {"v": 1, "playing": True, "mood": {"arousal": 0.3}}, now=0.0)
    feed.on_message("jukebox/analysis/audio/fast", {"v": 1, "level": 0.8, "bands": [0.5] * 32, "beat": True}, now=1.0)
    snap = feed.snapshot(now=1.05)
    assert snap.look == "ribbon" and snap.level == 0.8 and snap.beat() > 0.5
    assert len(snap.resample(16)) == 16 and len(snap.resample(36)) == 36
    feed.on_message("jukebox/analysis/audio/events", {"v": 1, "type": "drop"}, now=1.1)
    assert feed.snapshot(now=1.2).drop_age < 0.2
    quiet = feed.snapshot(now=3.0)                    # no fast frames: no sound
    assert quiet.level == 0.0 and quiet.bands == [0.0] * 32
    assert feed.snapshot(now=10.0).look == WAVES_LOOK


def test_resample_keeps_the_shape():
    snap = Snapshot([k / 31 for k in range(32)], 1.0, 9, 9, 0.5, "eq")
    down, up = snap.resample(16), snap.resample(36)
    assert down == sorted(down) and up == sorted(up)
    assert abs(up[0]) < 1e-9 and abs(up[-1] - 1) < 1e-9


def _snap(look, beat_age=9.0, drop_age=9.0, level=0.7):
    return Snapshot([0.2 + 0.6 * (k % 5) / 4 for k in range(32)], level, beat_age, drop_age, 0.5, look)


def _valid(frame, n):
    assert len(frame) == n
    assert all(len(px) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in px) for px in frame)


def test_accent_looks_render_crossfade_and_flash():
    looks = Looks(accent.positions())
    t = 0.0
    for look in ["idle", "eq", "eq", "ribbon", "ribbon", "idle"]:
        for _ in range(20):
            t += 0.025
            _valid(looks.frame(t, 0.025, _snap(look, beat_age=0.01)), accent.NUM_PIXELS)
    plain = looks.frame(t, 0.025, _snap("eq"))
    flash = looks.frame(t, 0.025, _snap("eq", drop_age=0.0))
    assert sum(map(sum, flash)) > sum(map(sum, plain))


def test_bar_looks_render():
    music = MusicLooks(Idle())
    t = 0.0
    for look in ["idle", "eq", "ribbon", "idle"]:
        for _ in range(40):
            t += 0.02
            _valid(music.frame(t, 0.02, _snap(look, beat_age=0.01)), matrix.NUM_PIXELS)


def test_accent_frames_are_fast_enough_for_40fps():
    looks = Looks(accent.positions())
    for look in ("eq", "ribbon"):
        looks.frame(0, 0.025, _snap(look))
        start = time.perf_counter()
        for i in range(40):
            looks.frame(i * 0.025, 0.025, _snap(look))
        per_frame = (time.perf_counter() - start) / 40
        assert per_frame < 0.02, f"{look}: {per_frame * 1000:.1f} ms a frame"


def test_malformed_messages_are_ignored():
    class Msg:
        def __init__(self, topic, payload):
            self.topic, self.payload = topic, payload
    feed = AudioFeed()
    for payload in (b"not json", b"[1, 2]", b'{"level": null}', b'{"bands": "x", "level": "loud"}'):
        feed._on_message(None, None, Msg("jukebox/analysis/audio/fast", payload))
    assert feed.snapshot().look == WAVES_LOOK


def test_idle_starts_on_the_waves_then_the_city_and_round_again():
    from services.jb_lights.audio_feed import IDLE_CITY_S, IDLE_WAVES_S
    p = LookPicker("auto")
    assert p.update(0, False, None) == "ribbon"
    assert p.update(IDLE_WAVES_S - 1, False, None) == "ribbon"
    assert p.update(IDLE_WAVES_S + 1, False, None) == "idle"
    assert p.update(IDLE_WAVES_S + IDLE_CITY_S - 1, False, None) == "idle"
    assert p.update(IDLE_WAVES_S + IDLE_CITY_S + 1, False, None) == "ribbon"


def test_music_during_the_waves_takes_over_and_the_idle_clock_restarts_after():
    from services.jb_lights.audio_feed import IDLE_AFTER_S, IDLE_WAVES_S
    p = LookPicker("auto")
    assert p.update(5, False, None) == "ribbon"
    look, t = play(p, 10, 2, 0.8)
    assert look == "eq"                                   # a lively song starts lively
    quiet = t + IDLE_AFTER_S
    assert p.update(quiet + 1, False, 0.8) == "ribbon"     # back on the waves
    assert p.update(quiet + IDLE_WAVES_S + 5, False, 0.8) == "idle"


def test_the_bar_idles_on_colour_stripes_across_all_36_strips():
    from services.jb_lights.levelbar import THEMES
    from services.jb_lights.main import BAR_THEME
    from services.jb_lights.themes.volume_bar_stripes import Stripes
    assert THEMES[BAR_THEME] is Stripes
    f = Stripes().frame(0, 0.02)
    assert len(f) == matrix.NUM_PIXELS
    first = [f[matrix.index(c, 0)] for c in range(matrix.COLS)]
    assert first[:6] == [(51, 0, 0), (0, 51, 0), (0, 0, 51), (51, 34, 0), (0, 51, 51), (51, 0, 51)]
    assert first[6:12] == first[:6] and first[35] == (51, 0, 51)
    assert all(f[matrix.index(c, r)] == first[c] for c in range(matrix.COLS) for r in range(matrix.ROWS))
