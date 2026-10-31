import json

from services.jb_lights import accent, owner, safety
from services.jb_lights.show import inputs as inp
from services.jb_lights.show.layers import ChainLayers
from services.jb_lights.stack import Stack

WEATHER = {"jb-weather": {"id": "jb-weather", "lights": {"signal": True, "cues": ["lightning", "rain"],
                                                         "overlays": ["rhythm", "keys"]}},
           "jb-quiet": {"id": "jb-quiet"}}
SETTINGS = owner.clean({})


def hub():
    return inp.Inputs(manifests=WEATHER)


def send(h, topic, payload, now=0.0):
    h.on_message(topic, json.dumps(payload).encode() if payload is not None else b"", now=now)


def test_a_module_gets_only_what_its_manifest_asked_for():
    h = hub()
    send(h, "jukebox/jb-weather/lights/signal", {"v": 1, "palette": ["#2040ff", "#ffffff"], "intensity": 0.5})
    send(h, "jukebox/jb-weather/lights/cue", {"cue": "lightning"})
    send(h, "jukebox/jb-weather/lights/cue", {"cue": "doorbell"})                    # not asked for
    send(h, "jukebox/jb-weather/lights/overlay/storm", {"on": "storyteller", "ttl": 5, "look": {"kind": "fill"}})
    send(h, "jukebox/jb-quiet/lights/signal", {"palette": ["#ff0000"]})              # asked for nothing
    send(h, "jukebox/jb-stranger/lights/cue", {"cue": "lightning"})                  # not installed
    st = h.state(now=0.1)
    assert st["signals"]["jb-weather"]["palette"] == [(32, 64, 255), (255, 255, 255)]
    assert [c["cue"] for c in st["cues"]] == ["lightning"]
    assert st["overlays"] == [] and "jb-quiet" not in st["signals"]
    r = h.report(now=0.1)
    assert "storyteller" in r["jb-weather"]["dropped"]
    assert "signal" in r["jb-quiet"]["dropped"] and r["jb-stranger"]["dropped"] == "not an installed module"


def test_bad_payloads_are_dropped_with_a_reason():
    allowed = inp.asked(WEATHER["jb-weather"])
    assert inp.parse("signal", {"v": 2}, allowed)[1].startswith("version 2")
    assert "palette" in inp.parse("signal", {"palette": ["red"]}, allowed)[1]
    assert "ttl" in inp.parse("overlay", {"on": "rhythm", "ttl": 9999, "look": {"kind": "fill"}}, allowed)[1]
    assert "look.kind" in inp.parse("overlay", {"on": "rhythm", "ttl": 9, "look": {"kind": "laser"}}, allowed)[1]
    h = hub()
    h.on_message("jukebox/jb-weather/lights/signal", b"{nope", now=0)
    assert h.report(now=0)["jb-weather"]["dropped"] == "not valid JSON"


def test_overlays_expire_and_an_empty_payload_clears():
    h = hub()
    send(h, "jukebox/jb-weather/lights/overlay/rain", {"on": "rhythm", "ttl": 5, "priority": 2,
                                                       "look": {"kind": "fill", "color": "#0000ff"}})
    send(h, "jukebox/jb-weather/lights/overlay/sun", {"on": "footlights", "ttl": 60, "look": {"kind": "fill"}})
    assert [o["name"] for o in h.state(now=1)["overlays"]] == ["sun", "rain"]          # by priority
    assert [o["name"] for o in h.state(now=6)["overlays"]] == ["sun"]                  # rain's ttl ran out
    send(h, "jukebox/jb-weather/lights/overlay/sun", None, now=7)
    assert h.state(now=7)["overlays"] == []


def test_the_layers_draw_signals_cues_and_overlays():
    h = hub()
    lay = ChainLayers("accent", accent.positions())
    base = [(100, 100, 100)] * accent.NUM_PIXELS
    assert lay.apply(base, 0, h.state(now=0), SETTINGS) is base                       # nothing: untouched
    send(h, "jukebox/jb-weather/lights/signal", {"palette": ["#0000ff"], "intensity": 0.5})
    out = lay.apply(base, 0, h.state(now=0), SETTINGS)
    assert out[0][2] > out[0][0] and max(out[0]) <= 100                                # tinted blue, dimmed
    off = owner.clean({"inputs": {"jb-weather": {"enabled": False}}})
    assert lay.apply(base, 0, h.state(now=0), off) == base                             # the owner turned it off
    h2 = hub()
    send(h2, "jukebox/jb-weather/lights/overlay/rain", {"on": "rhythm", "ttl": 5,
                                                        "look": {"kind": "fill", "color": "#ff0000"}})
    out = lay.apply(base, 1, h2.state(now=1), SETTINGS)
    assert out[0] == (100, 100, 100) and out[accent.UPPER] == (255, 0, 0)            # footlights only


def test_a_cue_becomes_what_the_show_says():
    h = hub()
    send(h, "jukebox/jb-weather/lights/cue", {"cue": "lightning"}, now=10.0)
    lay = ChainLayers("accent", accent.positions())
    base = [(0, 0, 0)] * accent.NUM_PIXELS

    class Show:
        inputs = {"cues": {"lightning": "flicker"}}
    assert lay.apply(base, 10.02, h.state(now=10.02), SETTINGS, Show)[0] == (255, 255, 255)
    assert lay.apply(base, 10.10, h.state(now=10.10), SETTINGS, Show)[0] == (0, 0, 0)  # between flickers
    Show.inputs = {"cues": {"lightning": "ignore"}}
    assert lay.apply(base, 10.02, h.state(now=10.02), SETTINGS, Show)[0] == (0, 0, 0)


def test_the_keypad_takes_white_on_its_white_led():
    h = hub()
    send(h, "jukebox/jb-weather/lights/overlay/k", {"on": "keys", "ttl": 5, "look": {"kind": "fill"}})
    lay = ChainLayers("buttons", [0.5] * 23, rgbw=True)
    out = lay.apply([(0, 0, 0, 0)] * 23, 1, h.state(now=1), SETTINGS)
    assert out[0] == (0, 0, 0, 255)


def test_safety_caps_brightness_quiet_hours_and_flashes():
    s = safety.Safety()
    st = owner.clean({"brightness": 0.5, "quiet": {"enabled": True, "from": "22:00", "to": "07:00", "brightness": 0.2}})
    assert s.apply([(200, 200, 200)], 0, 0.025, st, minutes=12 * 60) == [(100, 100, 100)]
    assert safety.Safety().apply([(200, 200, 200)], 0, 0.025, st, minutes=23 * 60) == [(40, 40, 40)]
    assert safety.Safety(floor=0.3).brightness(st, minutes=23 * 60) == 0.3            # the keypad stays readable
    s, full, dark = safety.Safety(), [(255, 255, 255)] * 10, [(0, 0, 0)] * 10
    flashes = 0
    for k in range(8):                                                                 # 8 flashes in 0.8 s
        s.apply(dark, k * 0.1, 0.05, SETTINGS)
        flashes += s.apply(full, k * 0.1 + 0.05, 0.05, SETTINGS)[0][0] == 255
    assert flashes == safety.MAX_FLASHES
    low = owner.clean({"low_flash": True})
    s = safety.Safety()
    s.apply(dark, 0, 0.025, low)
    assert s.apply(full, 0.025, 0.025, low)[0][0] < 30                                 # a swell, not a flash


def test_the_stack_needs_no_inputs_to_be_safe():
    class Lib:
        settings = owner.clean({"brightness": 0.5})
        def refresh(self, now=None): pass

    class Dir:
        library, style = Lib(), None
    st = Stack("levelbar", [(0, 0)] * 180, None, Dir())
    assert st([(200, 0, 0)] * 180, now=0)[0] == (100, 0, 0)


def test_manifest_lights_are_checked_and_reviewed():
    assert inp.check_manifest_lights(WEATHER["jb-weather"]["lights"]) == []
    assert inp.check_manifest_lights({"signal": "yes", "cues": ["Bad Name"], "overlays": ["roof"], "x": 1})
    assert inp.check_manifest_lights({"cues": ["Bad Name"], "overlays": ["roof"]})[0].startswith("lights.cues")
    text = " ".join(inp.review_lights(WEATHER["jb-weather"]["lights"]))
    assert "lightning" in text and "keypad" in text and "never while you're choosing" in text
