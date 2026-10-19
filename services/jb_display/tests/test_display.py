from services.jb_display import display as d
from services.jb_display.main import output_info


def test_clean_replaces_what_cant_be_used():
    assert d.clean(None) == d.DEFAULTS
    s = d.clean({"inset": {"top": 40, "left": -5, "right": "x", "bottom": True}, "rotation": 45, "sleep_minutes": 7})
    assert s == {"inset": {"top": 40, "right": 0, "bottom": 0, "left": 0}, "rotation": 0, "sleep_minutes": 0}
    assert d.clean({"rotation": 270, "sleep_minutes": 30})["rotation"] == 270


def test_a_side_takes_at_most_a_quarter_of_the_screen():
    assert d.limits((1920, 1080)) == {"top": 270, "bottom": 270, "left": 480, "right": 480}
    assert d.clamp({"top": 999, "left": 12}, (1920, 1080)) == {"top": 270, "right": 0, "bottom": 0, "left": 12}
    # Turned on its side, the screen is tall.
    assert d.logical_size((1920, 1080), 90) == (1080, 1920)
    assert d.limits(d.logical_size((1920, 1080), 90))["top"] == 480


def test_rotation_keeps_each_side_on_the_same_part_of_the_glass():
    inset = {"top": 1, "right": 2, "bottom": 3, "left": 4}
    # Turned 90 degrees clockwise, the picture's top is where its right was.
    assert d.remap(inset, 0, 90) == {"top": 2, "right": 3, "bottom": 4, "left": 1}
    assert d.remap(inset, 0, 180) == {"top": 3, "right": 4, "bottom": 1, "left": 2}
    assert d.remap(d.remap(inset, 0, 270), 270, 0) == inset
    assert d.remap(inset, 90, 90) == inset


def test_sway_layout():
    s = {"inset": {"top": 60, "right": 40, "bottom": 20, "left": 100}, "rotation": 0, "sleep_minutes": 0}
    assert d.sway_layout(s) == "output * transform normal"
    assert d.sway_layout({**s, "rotation": 180}) == "output * transform 180"


def test_sleep_waits_for_idle_and_no_music():
    sl = d.Sleep(0)
    assert not sl.due(299, 5)
    assert sl.due(300, 5) and sl.asleep
    assert not sl.due(400, 5)                 # already asleep
    assert sl.activity(410) and not sl.asleep  # the knob wakes it
    assert not sl.activity(411)                # already awake
    assert not sl.due(800, 0)                  # never


def test_music_keeps_the_screen_on_and_wakes_it():
    sl = d.Sleep(0)
    sl.source("phone", True, 0)
    assert not sl.due(10_000, 5)
    sl.source("phone", False, 10_000)          # idle counts from the music stopping
    assert not sl.due(10_100, 5)
    assert sl.due(10_300, 5)
    assert sl.source("local", True, 10_400) and not sl.asleep


def test_output_info_is_the_active_screen():
    outputs = [{"name": "HDMI-A-2", "active": False},
               {"name": "HDMI-A-1", "active": True, "make": "Dell Inc.", "model": "DELL P2414H",
                "current_mode": {"width": 1920, "height": 1080, "refresh": 60000}}]
    assert output_info(outputs) == {"name": "HDMI-A-1", "make": "Dell Inc.", "model": "DELL P2414H",
                                    "width": 1920, "height": 1080, "refresh": 60.0}
    assert output_info([]) is None
