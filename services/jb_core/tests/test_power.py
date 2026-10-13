from services.jb_core.power import MENU_TIMEOUT_S, PowerMenu


def test_hold_while_playing_skips_the_song():
    assert PowerMenu().hold(0, playing=True) == ["skip"]


def test_hold_while_idle_opens_the_menu():
    m = PowerMenu()
    assert m.hold(0, playing=False) == ["open"]
    assert m.long_hold(3, playing=False) == []          # already open


def test_keep_holding_while_playing_skips_then_pauses_and_opens():
    m = PowerMenu()
    assert m.hold(1, playing=True) == ["skip"]
    assert m.long_hold(4, playing=True) == ["pause", "open"]
    assert m.holding_queue


def test_cancel_resumes_what_the_menu_paused():
    m = PowerMenu()
    m.hold(1, playing=True)
    m.long_hold(4, playing=True)
    assert m.choose("cancel") == ["close", "resume"]
    assert not m.holding_queue


def test_cancel_with_nothing_paused_just_closes():
    m = PowerMenu()
    m.hold(0, playing=False)
    assert m.choose("cancel") == ["close"]


def test_reboot_and_shutdown_clear_the_queue_first():
    for choice in ("reboot", "shutdown"):
        m = PowerMenu()
        m.long_hold(4, playing=True)
        assert m.choose(choice) == ["close", "clear", choice]


def test_reset_click_cancels_the_menu():
    m = PowerMenu()
    m.long_hold(4, playing=True)
    assert m.reset_click() == ["close", "resume"]
    assert PowerMenu().reset_click() == []                 # no menu: nothing


def test_menu_times_out_as_a_cancel():
    m = PowerMenu()
    m.long_hold(4, playing=True)
    assert m.tick(4 + MENU_TIMEOUT_S - 1) == []
    assert m.tick(4 + MENU_TIMEOUT_S) == ["close", "resume"]


def test_choices_without_a_menu_or_unknown_do_nothing():
    m = PowerMenu()
    assert m.choose("reboot") == []
    m.hold(0, playing=False)
    assert m.choose("explode") == []
