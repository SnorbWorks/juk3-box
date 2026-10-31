"""The song-aware show: the plan for a song (show/plan.py) and the director
following it (show/director.py) -- the show that fits, one colour family,
the song's own pace, a look per section that comes back with it, the
accents, and a learned song's map."""

import time

from services.jb_lights import accent, matrix
from services.jb_lights.audio_feed import Raw
from services.jb_lights.show import plan as planner
from services.jb_lights.show import styles
from services.jb_lights.show.director import LATE_S, LEAD_S, Director
from services.jb_lights.show.library import Library
from services.jb_lights.show.parts import AccentShow, Bar, BarShow, Foot, Upper

MOODY = {"energy": 0.35, "valence": 0.2, "tender": 0.2, "aggressive": 0.1, "groove": 0.3, "moody": 0.85,
         "pace": 0.3, "voice": 0.7, "era": "10s", "words": ["moody", "mid-tempo", "vocals"]}
LOVE = {"energy": 0.3, "valence": 0.6, "tender": 0.9, "aggressive": 0.0, "groove": 0.2, "moody": 0.3,
        "pace": 0.1, "voice": 0.8, "era": "90s", "words": ["love song", "slow", "vocals"]}
EIGHTIES = {"energy": 0.6, "valence": 0.6, "tender": 0.1, "aggressive": 0.1, "groove": 0.55, "moody": 0.3,
            "pace": 0.62, "voice": 0.6, "era": "80s", "words": ["mid-tempo", "vocals", "80s"]}
HARD = {"energy": 0.9, "valence": 0.4, "tender": 0.0, "aggressive": 0.75, "groove": 0.3, "moody": 0.3,
        "pace": 0.75, "voice": 0.6, "era": "00s", "words": ["hard", "mid-tempo", "vocals"]}


def test_the_show_that_fits_the_song_is_chosen():
    lib = Library({})
    pick = lambda feel, genre=None, tags=(), seed=0: lib.choose(feel, genre, list(tags), 0.6, seed)[0].name
    assert pick(MOODY, tags=["indie"]) == "moody"
    assert pick(LOVE, "pop", ["love songs"]) == "love"
    assert pick(EIGHTIES, tags=["new wave"]) == "starfield"
    assert pick(HARD, "rock", ["hard rock"]) in ("rock-show", "metal", "rock")
    assert all(pick(LOVE, "pop", ["love songs"], seed) == "love" for seed in range(5))   # it isn't luck
    show, why = lib.choose(LOVE, "pop", ["love songs"], 0.4, 0)
    assert "love song" in why and "pop" in why
    # with no genre the song is only lively, not pop: its 80s feel alone finds the stars
    show, why = lib.choose(EIGHTIES, None, [], 0.6, 0)
    assert show.name == "starfield" and "pop" not in why


def test_a_pin_is_the_look_and_an_owners_rotation_limits_the_choice():
    show, why = Library({"pin": "oldies"}).choose(HARD, "rock", [], 0.8, 0)
    assert show.name == "oldies" and why[0] == "pinned"
    assert Library({"rotations": {"chill": ["jazz", "chill"]}}).choose(MOODY, "ambient", [], 0.3, 0)[0].name in ("jazz", "chill")
    # an idle show (no genre, no feel: Weather's) never plays to a song
    assert all(st.groups or st.fits for st in styles.STYLES.values())


def test_a_slow_tender_song_moves_slowly_and_smoothly_and_never_flashes():
    slow = planner.Plan(styles.STYLES["pop"], LOVE, tempo=68, seed=1)
    assert slow.rate <= 0.5 and slow.smooth >= 0.75 and slow.release >= 0.3 and slow.crossfade > 2
    assert not slow.flash and slow.accents["drop"] == "swell" and slow.accents["crash"] == "shimmer"
    assert slow.drive == "level"
    fast = planner.Plan(styles.STYLES["pop"], HARD, tempo=128, seed=1)
    assert fast.rate >= 1 and fast.smooth <= 0.3 and fast.flash and fast.accents["drop"] == "flash"
    assert planner.Plan(styles.STYLES["pop"], {**HARD, "pace": 0.95}, tempo=174).rate <= 1.04   # half time up there


def test_a_song_keeps_one_colour_family_and_a_moody_one_is_the_blue_one():
    rock = styles.STYLES["rock"]
    moody = planner.Plan(rock, MOODY, 100, seed=3)
    assert moody.palette == min(rock.palettes, key=planner.warmth)            # blue and white
    happy = {**HARD, "valence": 0.9, "tender": 0.5}
    assert all(planner.warmth(planner.Plan(rock, happy, 100, seed=s).palette) > 0.5 for s in range(6))
    neither = dict(planner.TYPICAL)                                          # leaning neither way: any of them
    assert {planner.Plan(rock, neither, 100, seed=s).palette for s in range(12)} == set(rock.palettes)
    a, b = moody.look({"label": "A", "role": "verse", "size": 0.5}), moody.look({"label": "B", "role": "verse", "size": 0.5})
    assert set(a.palette) == set(b.palette) == set(moody.palette)            # sections vary the family, not swap it


def test_each_section_gets_a_look_by_its_character_and_the_same_label_gets_it_back():
    p = planner.Plan(styles.STYLES["pop"], EIGHTIES, 120, seed=5)
    verse = p.look({"label": "A", "role": "verse", "n": 1, "size": 0.5, "voice": 0.8, "drums": 0.6})
    chorus = p.look({"label": "B", "role": "chorus", "n": 1, "size": 0.9, "voice": 0.8, "drums": 0.8})
    assert verse.character == "vocal" and verse.scene["upper"] in ("eq", "wash")      # the EQ look for the voice
    assert chorus.character == "big" and chorus.tier == "peak" and chorus.gain > verse.gain
    again = p.look({"label": "A", "role": "verse", "n": 2, "size": 0.5, "voice": 0.8, "drums": 0.6})
    assert again.scene == verse.scene and again.palette == verse.palette and again.gain > verse.gain
    last = p.look({"label": "B", "role": "chorus", "n": 3, "size": 1.0, "last": True})
    assert last.scene == chorus.scene and last.palette == styles.STYLES["pop"].peak
    assert planner.character({"role": "breakdown", "size": 0.2}) == "break"
    assert planner.character({"role": "pre-chorus", "size": 0.6}) == "build"
    assert planner.character({"role": "intro", "size": 0.2}) == "quiet"
    assert planner.character({"role": "verse", "size": 0.6, "voice": 0.1, "drums": 0.8}) == "drive"


def raw(**kw):
    r = Raw()
    r.title, r.artist, r.level, r.tempo, r.phase, r.phase_at = "Song", "Band", 0.7, 120.0, 0.0, 0.0
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def run(d, r, start, seconds, step=0.025):
    cues, t = None, start
    while t < start + seconds:
        cues = d.update(t, r)
        t += step
    return cues, t


def section(r, t, **s):
    r.section, r.section_at = {"type": "section", "source": "live", **s}, t


def test_the_director_follows_the_sections_and_the_chorus_looks_like_the_chorus_again():
    d, r = Director(Library({"pin": "pop"})), raw(feel=EIGHTIES, feel_at=0.0)
    _, t = run(d, r, 0, 1)
    looks = {}
    for label, role, size in (("A", "verse", 0.5), ("B", "chorus", 0.9), ("A", "verse", 0.5), ("B", "chorus", 0.95)):
        section(r, t, label=label, role=role, size=size, voice=0.8, drums=0.7, n=1 + sum(1 for k in looks if k[0] == label))
        cues, t = run(d, r, t, 4)
        looks.setdefault(label, []).append((cues.scene, cues.palette, cues.character))
    assert looks["A"][0] == looks["A"][1] and looks["B"][0][0] == looks["B"][1][0]
    assert looks["A"][0][2] == "vocal" and looks["B"][0][2] == "big"


def test_a_crash_splashes_a_ballad_only_shimmers_and_a_drop_slams_in():
    d, r = Director(Library({"pin": "rock"})), raw(feel=HARD, feel_at=0.0)
    _, t = run(d, r, 0, 1)
    section(r, t, label="A", role="chorus", size=0.9, drums=0.8)
    _, t = run(d, r, t, 0.5)
    r.crash_at, r.crash_v = t, 0.9
    cues, t = run(d, r, t, 0.1)
    assert cues.splashes and cues.splashes[0][5] == "splash"
    up = AccentShow(accent.positions())
    lit = lambda c: sum(map(sum, up.frame(t, 0.025, type("S", (), {"cues": c, "bands": [0.3] * 32, "level": 0.5,
                                                                  "resample": lambda self, n: [0.3] * n})())))
    with_splash = lit(cues)
    cues.splashes = []
    assert with_splash > lit(cues)
    r.drop_at = t                                               # rock: the drop slams the brightness up
    cues, t = run(d, r, t, 0.05)
    assert cues.lift > 0.8 and cues.flash < 0.01
    d, r = Director(Library({"pin": "rock"})), raw(feel=LOVE, feel_at=0.0, tempo=68.0)
    _, t = run(d, r, 0, 1)
    r.crash_at, r.crash_v = t, 0.9
    r.drop_at = t
    cues, t = run(d, r, t, 0.3)
    assert cues.splashes[0][5] == "shimmer" and cues.flash == 0 and 0 < cues.lift < 0.7    # a swell, rising


def test_a_slow_song_steps_once_a_bar_and_a_fast_one_once_a_beat():
    for feel, tempo, most in ((LOVE, 68.0, 0.26), (EIGHTIES, 120.0, 1.01)):
        d, r = Director(Library({"pin": "pop"})), raw(feel=feel, feel_at=0.0, tempo=tempo)
        cues, _ = run(d, r, 0, 10)
        assert cues.beat > 9 and cues.move <= most * cues.beat + 0.1, (feel["words"], cues.move, cues.beat)


def mapped(**kw):
    m = {"song": "s1", "known": True, "tempo": 120.0,
         "sections": [{"start": 0.0, "label": "A", "n": 1, "role": "intro", "energy": 0.2, "voice": 0.0, "drums": 0.3},
                      {"start": 8.0, "label": "B", "n": 1, "role": "verse", "energy": 0.5, "voice": 0.8, "drums": 0.6},
                      {"start": 16.0, "label": "C", "n": 1, "role": "chorus", "energy": 0.9, "voice": 0.8, "drums": 0.8}],
         "moments": [{"t": 15.0, "type": "gap"}, {"t": 16.0, "type": "drop"}]}
    m.update(kw)
    return m


def test_a_learned_song_follows_its_map_a_moment_early_and_sees_whats_coming():
    d = Director(Library({"pin": "pop"}))
    r = raw(feel=EIGHTIES, feel_at=0.0, song="s1", known=True, map=mapped(), map_at=0.0, synced=True)
    t, chars = 0.0, {}
    while t < 17.0:
        r.pos, r.pos_at = t, t                                 # the song position, locked to the sound
        cues = d.update(t, r)
        chars.setdefault(cues.character, t)
        if 15.0 - LATE_S["gap"] - LEAD_S - 0.03 <= t <= 15.05:
            dark = cues.black
        t += 0.025
    assert abs(chars["vocal"] - (8.0 - LEAD_S)) < 0.03 and abs(chars["big"] - (16.0 - LEAD_S)) < 0.03
    assert dark > 0.5                                          # dark as the sound falls away, not 0.1 s late
    # the bars before the chorus: the build into it
    d = Director(Library({"pin": "pop"}))
    r = raw(feel=EIGHTIES, feel_at=0.0, song="s1", known=True, map=mapped(moments=[]), map_at=0.0, synced=True)
    for t in (12.0, 15.0):
        r.pos, r.pos_at = t, t
        cues = d.update(t, r)
    assert cues.tension > 0.5


def test_a_live_moment_the_map_already_played_is_not_played_twice():
    d = Director(Library({"pin": "pop"}))
    r = raw(feel=EIGHTIES, feel_at=0.0, song="s1", known=True, map=mapped(), map_at=0.0, synced=True)
    t = 15.5
    while t < 16.1:
        r.pos, r.pos_at = t, t
        d.update(t, r)
        t += 0.025
    flashes = len(d.flashes)
    r.drop_at = t                                               # the analyzer calls the same drop, live
    r.pos, r.pos_at = t, t
    d.update(t, r)
    assert flashes == 1 and len(d.flashes) == 1


def test_the_new_moves_draw_fast_on_every_part():
    d, r = Director(Library({"pin": "starfield"})), raw(feel=EIGHTIES, feel_at=0.0)
    cues, t = run(d, r, 0, 2)
    snap = type("S", (), {"cues": cues, "bands": [0.4] * 32, "level": 0.6, "resample": lambda self, n: [0.4] * n})()
    parts = ((Upper(accent.positions()), accent.UPPER), (Foot(), accent.FOOT), (Bar(), matrix.NUM_PIXELS))
    for renderer, n in parts:
        for scene in ("starfield", "flow") + (("beams",) if not isinstance(renderer, Bar) else ()):
            out = getattr(renderer, scene)(cues, snap, t, 0.025)
            assert len(out) == n and all(len(c) == 3 and min(c) >= 0 for c in out), scene
    # the whole cabinet, starfield and splashes: well inside a 40 fps frame
    up, bar = AccentShow(accent.positions()), BarShow()
    cues.splashes = [(0.2, 11.0, 6.0, 1.0, (255.0, 255.0, 255.0), "splash"),
                     (0.5, 5.0, 3.0, 0.8, (200.0, 200.0, 255.0), "shimmer")]
    start = time.perf_counter()
    for k in range(40):
        up.frame(t + k * 0.025, 0.025, snap)
        bar.frame(t + k * 0.025, 0.025, snap)
    assert (time.perf_counter() - start) / 40 < 0.02


def test_every_built_in_shows_section_scenes_draw():
    feel = Director(Library({})).update(0.0, raw())
    snap = type("S", (), {"cues": feel, "bands": [0.4] * 32, "level": 0.6, "resample": lambda self, n: [0.4] * n})()
    parts = {"upper": Upper(accent.positions()), "foot": Foot(), "bar": Bar()}
    for st in styles.STYLES.values():
        for part, renderer in parts.items():
            for ch in styles.CHARACTERS:
                for scene in st.scenes_for(part, ch):
                    assert len(getattr(renderer, scene)(feel, snap, 1.0, 0.025)) == renderer.size, (st.name, scene)
            assert st.scenes_for("keys", "vocal")[0] in styles.SCENES["keys"]


def test_the_lights_report_the_songs_plan_its_look_and_its_map():
    from services.jb_lights.audio_feed import TOPIC, AudioFeed, LookPicker
    feed = AudioFeed(picker=LookPicker("show"), director=Director(Library({"pin": "pop"})))
    assert feed.report(now=0.0) is None                                     # nothing yet
    feed.on_message(f"{TOPIC}/slow", {"playing": True, "tempo": 120.0, "identity": {"title": "Song", "artist": "Band"},
                                      "song": "s1", "known": True, "feel": EIGHTIES}, now=0.0)
    feed.on_message(f"{TOPIC}/map", mapped(), now=0.0)
    t = 0.0
    while t < 9.0:
        feed.on_message(f"{TOPIC}/fast", {"level": 0.6, "bands": [0.4] * 32, "phase": (t * 2) % 1.0, "pos": t,
                                          "synced": True}, now=t)
        feed.snapshot(now=t)
        t += 0.05
    r = feed.report(now=t)
    assert (r["title"], r["plan"]["show"], r["plan"]["why"][0]) == ("Song", "pop", "pinned")
    assert r["look"]["character"] == "vocal" and r["look"]["label"] == "B"
    assert r["map"]["known"] and r["map"]["following"] and abs(r["map"]["pos"] - 9.0) < 0.1
    assert [s["character"] for s in r["map"]["sections"]] == ["quiet", "vocal", "big"]
