import time

from services.jb_lights import accent, buttons, matrix
from services.jb_lights.accent_lights import Looks
from services.jb_lights.audio_feed import AudioFeed, LookPicker, Raw
from services.jb_lights.levelbar import MusicLooks
from services.jb_lights.show import styles
from services.jb_lights.show.director import MAX_FLASHES_PER_S, Director
from services.jb_lights.show.parts import Bar, Foot, Keys, Upper
from services.jb_lights.themes.buttons_music import ButtonsMusic
from services.jb_lights.themes.volume_bar_idle import Idle

A = "jukebox/analysis/audio/"


def test_genre_and_tags_pick_the_style_and_mood_is_the_fallback():
    assert styles.pick("hip-hop", [], 0.7).name == "hiphop"
    assert styles.pick(None, ["seen live", "Funk", "80s"], 0.7).name == "soul"
    assert styles.pick("dubstep", ["edm"], 0.9).name == "bass"
    assert styles.pick(None, [], 0.2).name == "chill"
    assert styles.pick(None, ["polka"], 0.8).name == "pop"
    assert styles.seed("Song", "Band") == styles.seed("Song", "Band") != styles.seed("Other", "Band")


def test_every_style_names_scenes_the_parts_can_draw():
    for st in styles.STYLES.values():
        assert st.palettes and st.peak and 0 <= st.light[0] < st.light[1] <= 1
        for part, cls in (("upper", Upper), ("foot", Foot), ("bar", Bar)):
            tiers = getattr(st, part)
            assert set(tiers) == {"calm", "main", "peak"}, (st.name, part)
            for options in tiers.values():
                assert options and all(callable(getattr(cls, s, None)) for s in options), (st.name, part, options)
        assert st.keys in ("sparkle", "eq", "pulse", "glow")
        assert st.drive in ("kick", "snare", "offbeat", "level") and st.gap in ("blackout", "dim", "")


def raw(**kw):
    r = Raw()
    r.title, r.level, r.tempo, r.phase, r.phase_at = "Song", 0.7, 120.0, 0.0, 0.0
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def run(director, r, start, seconds, step=0.025):
    cues, t = None, start
    while t < start + seconds:
        cues = director.update(t, r)
        t += step
    return cues, t


def test_a_gap_blacks_out_and_the_hit_flashes_white():
    d, r = Director(), raw(genre="edm")
    cues, t = run(d, r, 0, 2)
    assert cues.style.name == "edm" and cues.black == 0 and cues.flash < 0.01
    r.gap_at = t
    cues, t = run(d, r, t, 0.2)
    assert cues.black == 1.0
    r.hit_at, r.hit_after_gap, r.level = t, True, 0.9
    cues = d.update(t + 0.025, r)
    assert cues.black < 0.9 and cues.flash > 0.9
    cues, _ = run(d, r, t, 1.0)
    assert cues.black == 0 and cues.flash < 0.05


def test_a_gap_never_holds_the_cabinet_dark_for_long():
    d, r = Director(), raw(genre="edm", level=0.3)
    _, t = run(d, r, 0, 1)
    r.gap_at = t
    cues, _ = run(d, r, t, 2.0)
    assert cues.black == 0


def test_flashes_are_limited_for_safety_and_gentle_styles_never_flash():
    d, r = Director(), raw(genre="metal")
    _, t = run(d, r, 0, 1)
    flashes = 0
    for k in range(10):                               # ten big hits in one second
        r.hit_at, r.hit_strength = t, 1.0
        before = d.flash
        d.update(t, r)
        flashes += d.flash > before + 0.3
        t += 0.1
    assert flashes <= MAX_FLASHES_PER_S
    d, r = Director(), raw(genre="jazz")
    _, t = run(d, r, 0, 1)
    r.drop_at, r.hit_at, r.hit_after_gap = t, t, True
    assert d.update(t, r).flash == 0


def test_sections_move_the_palette_and_a_drop_brings_the_peak():
    d, r = Director(), raw(genre="pop")
    cues, t = run(d, r, 0, 2)
    first = cues.palette
    r.section_at, r.section_energy = t, "same"
    cues, t = run(d, r, t, 0.5)
    assert cues.palette != first and cues.tier == "main"
    r.drop_at = t
    cues, t = run(d, r, t, 0.5)
    assert cues.tier == "peak" and cues.palette == styles.STYLES["pop"].peak
    r.build = "breakdown"
    cues, t = run(d, r, t + 20, 2)
    assert cues.tier == "calm" and cues.cool > 0.9


def test_the_kick_drives_the_footlights_at_most_three_times_a_second():
    d, r = Director(), raw(genre="edm")
    _, t = run(d, r, 0, 1)
    pulses = 0
    for k in range(40):                               # a kick every 25 ms for a second
        r.drum_at["kick"], r.drum_v["kick"] = t, 1.0
        before = d.pulse["kick"]
        d.update(t, r)
        pulses += d.pulse["kick"] > before + 0.3
        t += 0.025
    assert 1 <= pulses <= 3


def test_a_new_song_restyles_and_the_genre_arriving_late_restyles_too():
    d = Director()
    r = raw(title="One", genre=None, tags=[], arousal=0.8)
    assert run(d, r, 0, 1)[0].style.name == "pop"
    r.genre, r.tags, r.genre_at = "country", ["country"], 1.0
    assert run(d, r, 1, 1)[0].style.name == "country"
    r.title, r.genre, r.tags = "Two", "metal", ["metal"]
    assert run(d, r, 2, 1)[0].style.name == "metal"


def stream(feed, t, genre="edm", kick=False):
    feed.on_message(A + "slow", {"v": 1, "playing": True, "tempo": 128.0, "mood": {"arousal": 0.8},
                                 "identity": {"title": "Song", "artist": "Band"}, "genre": genre,
                                 "tags": [genre]}, now=t)
    feed.on_message(A + "fast", {"v": 1, "level": 0.8, "bands": [0.6] * 32, "beat": kick, "kick": 1.0 if kick else 0.0,
                                 "snare": 0.0, "hat": 0.9, "build": "none", "phase": 0.0, "bar_beat": 0}, now=t)


def _valid(frame, n):
    assert len(frame) == n
    assert all(len(px) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in px) for px in frame)


def test_every_style_renders_on_every_part_through_the_feed():
    for name in styles.STYLES:
        feed = AudioFeed(picker=LookPicker("show"))
        looks, bar, keys = Looks(accent.positions()), MusicLooks(Idle()), ButtonsMusic()
        genre = next(g for g, s in styles.GENRE_STYLE.items() if s == name)
        t = 0.0
        for k in range(120):
            t += 0.025
            stream(feed, t, genre, kick=k % 20 == 0)
            if k == 60:
                feed.on_message(A + "events", {"v": 1, "type": "drop"}, now=t)
            snap = feed.snapshot(now=t)
            assert snap.look == "show" and snap.cues.style.name == name
            _valid(looks.frame(t, 0.025, snap), accent.NUM_PIXELS)
            _valid(bar.frame(t, 0.025, snap), matrix.NUM_PIXELS)
            colours = keys.frame(0.025, snap)
            assert len(colours) == buttons.NUM_PIXELS and all(len(c) == 4 for c in colours.values())


def test_the_footlights_jump_on_the_kick():
    feed = AudioFeed(picker=LookPicker("show"))
    looks = Looks(accent.positions())
    t = 0.0
    for k in range(80):
        t += 0.025
        stream(feed, t, "rock")
        looks.frame(t, 0.025, feed.snapshot(now=t))
    feed.raw.drum_at["snare"], feed.raw.drum_v["snare"] = t, 1.0     # rock: the snare drives
    foot = lambda f: sum(map(sum, f[accent.UPPER:]))
    hit = foot(looks.frame(t, 0.025, feed.snapshot(now=t)))
    for _ in range(16):
        t += 0.025
        stream(feed, t, "rock")
        later = foot(looks.frame(t, 0.025, feed.snapshot(now=t)))
    assert hit > 1.5 * later


def test_show_frames_are_fast_enough_for_40fps():
    feed = AudioFeed(picker=LookPicker("show"))
    looks = Looks(accent.positions())
    for name in ("edm", "oldies", "hip-hop"):
        t = 0.0
        stream(feed, t, name)
        looks.frame(t, 0.025, feed.snapshot(now=t))
        start = time.perf_counter()
        for i in range(40):
            t += 0.025
            stream(feed, t, name, kick=i % 10 == 0)
            looks.frame(t, 0.025, feed.snapshot(now=t))
        per_frame = (time.perf_counter() - start) / 40
        assert per_frame < 0.02, f"{name}: {per_frame * 1000:.1f} ms a frame"


def test_every_scene_draws():
    feed = AudioFeed(picker=LookPicker("show"))
    stream(feed, 0.0, "edm", kick=True)
    snap = feed.snapshot(now=0.0)
    parts = {"upper": (Upper(accent.positions()), accent.UPPER), "foot": (Foot(), accent.FOOT),
             "bar": (Bar(), matrix.NUM_PIXELS)}
    for st in styles.STYLES.values():
        for part, (renderer, n) in parts.items():
            for scene in {s for options in getattr(st, part).values() for s in options}:
                out = getattr(renderer, scene)(snap.cues, snap, 1.0, 0.025)
                assert len(out) == n and all(len(c) == 3 for c in out), (part, scene)
    for name in ("sparkle", "eq", "pulse", "glow"):
        snap.cues.scene["keys"] = name
        assert len(Keys().frame(snap.cues, snap, 0.025)) == buttons.NUM_PIXELS


def test_the_drum_kit_hits_each_drum_in_its_own_block():
    feed = AudioFeed(picker=LookPicker("show"))
    stream(feed, 0.0, "hip-hop")
    bar = Bar()
    kick_block = [matrix.index(c, r) for c in range(14, 23) for r in range(5)]
    snap = feed.snapshot(now=0.0)
    quiet = sum(map(sum, (bar.drumkit(snap.cues, snap, 0.0, 0.025)[i] for i in kick_block)))
    snap.cues.pulse["kick"] = 1.0
    hit = sum(map(sum, (bar.drumkit(snap.cues, snap, 0.0, 0.025)[i] for i in kick_block)))
    assert hit > 5 * quiet


def test_the_level_bar_changes_its_scene_through_a_song():
    d, r = Director(), raw(genre="edm")
    seen, t = set(), 0.0
    for _ in range(int(120 / 0.1)):                  # two minutes at 120 bpm
        seen.add(d.update(t, r).scene["bar"])
        t += 0.1
    assert len(seen) >= 3
