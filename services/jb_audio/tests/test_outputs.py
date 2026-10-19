import json

from shared import pipewire as pw
from services.jb_audio import outputs as o


def node(id_, name, media_class="Audio/Sink"):
    return {"id": id_, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": name, "media.class": media_class}}}


DUMP = [
    node(47, None, "Audio/Device"),
    node(58, "alsa_output.platform-soc_sound.stereo-fallback"),
    node(90, "jb-whole-home"),
    node(91, "jb-airplay"),
    node(82, "jb-analyzer", "Stream/Input/Audio"),
    {"id": 0, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
     "metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON",
                   "value": {"name": "jb-whole-home"}}]},
]


def test_finds_the_dac_and_nodes_by_name():
    assert pw.find_dac(DUMP)["id"] == 58
    assert pw.find(DUMP, "jb-airplay") == 91
    assert pw.find(DUMP, "nope") is None
    assert pw.find_dac([node(90, "jb-whole-home")]) is None


def test_default_sink_name():
    assert o.default_sink_name(DUMP) == "jb-whole-home"
    assert o.default_sink_name(DUMP[:-1]) is None


def test_airplay_pattern_matches_the_discovered_name_only():
    assert o.airplay_pattern("Inside") == "~^[0-9A-F]+@Inside$"
    # "Inside & Outside" must not be picked by a plain "Inside"
    assert o.airplay_pattern("Inside").endswith("@Inside$")
    assert o.airplay_pattern("Bar/Dining (2)") == "~^[0-9A-F]+@Bar/Dining \\(2\\)$"


def test_spa_string_escapes():
    assert o.spa_string('a"b\\c') == '"a\\"b\\\\c"'


def test_outputs_conf_wires_the_delay_to_the_dac_and_filters_airplay():
    s = o.Settings(whole_home=True, airplay="Inside", delay_ms=1580)
    conf = o.outputs_conf(s, "alsa_output.x")
    assert '"Delay (s)" = 1.58' in conf
    assert 'target.object = "alsa_output.x"' in conf
    assert 'node.name = "jb-whole-home" media.class = Audio/Sink' in conf
    # the SPA string doubles the regex's backslashes; none needed for "Inside"
    assert 'raop.name = "~^[0-9A-F]+@Inside$"' in conf
    assert 'node.name = "jb-airplay"' in conf
    assert conf.count("{") == conf.count("}") and conf.count("[") == conf.count("]")


def test_feed_never_falls_back_to_the_default_sink():
    cmd = " ".join(o.feed_cmd())
    assert cmd.count("node.dont-fallback=true") == 2
    assert "target.object=jb-whole-home" in cmd and "target.object=jb-airplay" in cmd


def test_feed_policy_lingers_then_lets_go():
    p = o.FeedPolicy(linger_s=30)
    assert not p.want_feed(0)
    p.update("phone", "playing", 10)
    assert p.want_feed(11)
    p.update("phone", "paused", 20)
    assert p.want_feed(45)       # 25 s after the last playing moment
    assert not p.want_feed(51)
    p.update("local", "playing", 60)
    p.update("phone", "playing", 61)
    p.update("local", "stopped", 62)
    assert p.want_feed(500)      # the phone is still playing


def test_settings_round_trip_and_clamp(tmp_path):
    path = tmp_path / "jb-audio.json"
    assert o.Settings.load(path) == o.Settings()
    o.Settings(whole_home=True, airplay="Den", delay_ms=99999, airplay_volume=3).clamped().save(path)
    s = o.Settings.load(path)
    assert (s.whole_home, s.airplay, s.delay_ms, s.airplay_volume) == (True, "Den", 4000, 1.0)
    path.write_text(json.dumps({"whole_home": True, "unknown": 1}))
    assert o.Settings.load(path).whole_home is True


def test_delay_params():
    assert o.delay_params(1500) == '{ params = [ "dl:Delay (s)" 1.5 "dr:Delay (s)" 1.5 ] }'
