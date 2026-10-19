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
    s = o.Settings(whole_home=True, airplay="Inside", measured_ms=1750, offset_ms=-20)
    conf = o.outputs_conf(s, "alsa_output.x")
    assert '"Delay (s)" = 1.73' in conf
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
    assert o.Settings().whole_home is False      # off unless the owner turns it on
    o.Settings(whole_home=True, airplay="Den", measured_ms=99999, offset_ms=-5000, airplay_volume=3).clamped().save(path)
    s = o.Settings.load(path)
    assert (s.whole_home, s.airplay, s.measured_ms, s.offset_ms, s.airplay_volume) == (True, "Den", 4000, -1000, 1.0)
    # an old file (fixed delay_ms) loads; the unknown key is dropped
    path.write_text(json.dumps({"whole_home": True, "delay_ms": 1580, "unknown": 1}))
    assert o.Settings.load(path).whole_home is True


def test_delay_is_measured_plus_offset_never_negative():
    assert o.Settings(measured_ms=1750, offset_ms=-20).delay_ms == 1730
    assert o.Settings(measured_ms=100, offset_ms=-500).delay_ms == 0


def airplay_dump(min_rate, rate=None):
    props = {"node.name": "jb-airplay", "media.class": "Audio/Sink"}
    if rate:
        props["audio.rate"] = rate
    return [{"id": 91, "type": "PipeWire:Interface:Node", "info": {"props": props, "params": {"Latency": [
        {"direction": "Input", "minQuantum": 0.0, "maxQuantum": 0.0, "minRate": min_rate, "maxRate": min_rate,
         "minNs": 0, "maxNs": 0},
        {"direction": "Output", "minQuantum": 0.0, "maxQuantum": 0.0, "minRate": 0, "maxRate": 0, "minNs": 0, "maxNs": 0}]}}}]


def test_reads_the_airplay_latency_the_protocol_reports():
    # Inside, connected (2026-10-18): 77175 samples at 44.1 kHz
    assert o.airplay_latency_ms(airplay_dump(77175)) == 1750
    assert o.airplay_latency_ms(airplay_dump(96000, rate=48000)) == 2000
    assert o.airplay_latency_ms(airplay_dump(0)) is None        # not connected
    assert o.airplay_latency_ms(DUMP) is None


def test_delay_params():
    assert o.delay_params(1500) == '{ params = [ "dl:Delay (s)" 1.5 "dr:Delay (s)" 1.5 ] }'
