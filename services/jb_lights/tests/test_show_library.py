import json

from services.jb_lights import owner
from services.jb_lights.show import styles
from services.jb_lights.show.library import Library
from services.jb_lights.show.parts import Bar, Foot, Upper


def show(**kw):
    d = json.loads((styles.BUILTIN_DIR / "pop.json").read_text())
    d.update({"id": "my-show", "name": "My show", "for": ["rock"]})
    d.update(kw)
    return d


def test_the_effect_library_is_what_the_parts_can_draw():
    for role, cls in (("storyteller", Upper), ("rhythm", Foot), ("meter", Bar)):
        for scene in styles.SCENES[role]:
            assert callable(getattr(cls, scene, None)), (role, scene)
    names = lambda cls: {n for n, v in vars(cls).items() if callable(v) and not n.startswith("_")} - {"frame"}
    assert names(Upper) == styles.SCENES["storyteller"]
    assert names(Foot) == styles.SCENES["rhythm"]
    assert names(Bar) == styles.SCENES["meter"]


def test_every_built_in_show_checks_out():
    assert len(styles.STYLES) == 17
    assert {"moody", "love", "starfield", "rock-show"} <= set(styles.STYLES)
    for f in styles.BUILTIN_DIR.glob("*.json"):
        assert styles.check(json.loads(f.read_text())) == [], f.name


def test_a_bad_show_file_says_what_is_wrong():
    assert styles.check(show()) == []
    probs = styles.check(show(id="Bad Id", palettes=[["red"]], strobe=5,
                              roles={**show()["roles"], "rhythm": {"calm": ["moonwalk"], "main": ["glow"], "peak": ["glow"]}}))
    text = " / ".join(probs)
    assert "id must be" in text and "palette 1" in text and "strobe" in text and "moonwalk" in text
    assert styles.check(show(format=3))[0].startswith("it's show format 3")
    assert styles.check(show(fits={"moody": 2, "eras": ["40s"], "colour": 1}, motion={"pace": "fast"},
                             accents={"crash": "boom"}, sections={"chorus": {}, "big": {"rhythm": ["moonwalk"]}}))
    assert styles.check(show(inputs={"cues": {"lightning": "explode"}}))
    assert styles.check(show(inputs={"palette": 0.3, "cues": {"lightning": "flicker"}})) == []


def test_uploaded_and_module_shows_join_the_rotations(tmp_path):
    shows, mods = tmp_path / "shows", tmp_path / "modules"
    shows.mkdir()
    (shows / "my-show.json").write_text(json.dumps(show()))
    (shows / "broken.json").write_text("{not json")
    mod = mods / "jb-weather"
    (mod / "shows").mkdir(parents=True)
    (mod / "module.json").write_text(json.dumps({"id": "jb-weather", "shows": ["shows/weather.json", "shows/other.json"]}))
    (mod / "shows" / "weather.json").write_text(json.dumps(show(id="weather", name="Weather", **{"for": ["chill"]})))
    (mod / "shows" / "other.json").write_text(json.dumps(show(id="not-mine")))
    lib = Library(settings_path=tmp_path / "settings.json", shows_dir=shows, modules_dir=mods)
    assert lib.shows["my-show"].source == "uploaded" and lib.shows["weather"].source == "jb-weather"
    assert "not-mine" not in lib.shows                      # a module's show is named after the module
    assert any("broken.json" in n for n in lib.notes) and any("not-mine" in n for n in lib.notes)
    assert lib.rotation("rock") == ["rock", "rock-show", "my-show"]   # the group's own first, then built-ins
    assert lib.rotation("chill") == ["chill", "moody", "weather"]


def test_the_owner_picks_rotations_disables_and_pins(tmp_path):
    path = tmp_path / "settings.json"
    lib = Library(settings_path=path, shows_dir=tmp_path / "none", modules_dir=tmp_path / "none")
    assert lib.rotation("metal") == ["metal", "arena", "rock-show"]
    path.write_text(json.dumps({"rotations": {"metal": ["arena"]}, "disabled": ["edm"]}))
    lib.refresh(force=True)
    assert lib.rotation("metal") == ["arena"]
    assert lib.rotation("edm") == ["arena"]                  # edm's own show is off
    assert lib.pick("metal", [], 0.8, seed=12345).name == "arena"
    path.write_text(json.dumps({"pin": "oldies"}))
    lib.refresh(force=True)
    assert lib.pick("metal", [], 0.8).name == "oldies"
    path.write_text(json.dumps({"pin": "no-such-show", "rotations": {"rock": ["gone"]}}))
    lib.refresh(force=True)
    assert lib.pick("rock", [], 0.8).name == "rock"         # nothing usable: the group's own


def test_the_settings_are_cleaned_whatever_the_file_says():
    s = owner.clean({"brightness": 7, "pin": "../etc", "quiet": {"enabled": True, "from": "25:00", "brightness": 0},
                     "inputs": {"jb-weather": {"enabled": False}, "Bad": {}}, "idle": 3, "extra": 1})
    assert s["brightness"] == 1.0 and s["pin"] is None and s["idle"] == "city" and "extra" not in s
    assert s["quiet"] == {"enabled": True, "from": "22:00", "to": "07:00", "brightness": 0.05}
    assert s["inputs"] == {"jb-weather": {"enabled": False, "strength": 1.0}}
    assert owner.input_gain(s, "jb-weather") == 0.0 and owner.input_gain(s, "jb-other") == 1.0
    assert owner.quiet_now(s, 23 * 60) and owner.quiet_now(s, 6 * 60) and not owner.quiet_now(s, 12 * 60)


def test_the_owner_can_choose_an_idle_show(tmp_path):
    from services.jb_lights.audio_feed import AudioFeed
    from services.jb_lights.show.director import Director
    path = tmp_path / "settings.json"
    lib = Library(settings_path=path, shows_dir=tmp_path / "none", modules_dir=tmp_path / "none")
    feed = AudioFeed(director=Director(library=lib))
    assert feed.snapshot(now=100.0).look in ("ribbon", "idle")              # the waves and the city
    path.write_text(json.dumps({"idle": "chill"}))
    lib.refresh(force=True)
    snap = feed.snapshot(now=101.0)
    assert snap.look == "idle-show" and snap.cues.style.name == "chill" and snap.cues.tier == "calm"
    path.write_text(json.dumps({"idle": "oldies"}))
    lib.refresh(force=True)
    assert feed.snapshot(now=102.0).cues.style.name == "oldies"
