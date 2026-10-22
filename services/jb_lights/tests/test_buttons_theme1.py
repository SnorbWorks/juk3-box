from services.jb_lights import buttons
from services.jb_lights.themes.buttons_theme1 import GREEN, RED, WHITE, Theme1


class Strip:
    def __init__(self):
        self.px = [None] * buttons.NUM_PIXELS

    def setPixelColor(self, i, c):
        self.px[i] = c

    def show(self):
        pass


def lit(typed, held=False):
    strip = Strip()
    theme = Theme1(strip)
    theme.reset_held = held
    theme.show_entry(typed)
    return {i: c for i, c in enumerate(strip.px) if c != WHITE}


def test_a_waiting_letter_is_green_with_reset_red():
    assert lit("B") == {buttons.index("B"): GREEN, buttons.RESET: RED}


def test_a_numbers_only_jukebox_lights_the_digits_typed_so_far():
    assert lit("21") == {buttons.index("2"): GREEN, buttons.index("1"): GREEN, buttons.RESET: RED}


def test_idle_is_all_white_unless_reset_is_held():
    assert lit(None) == {}
    assert lit(None, held=True) == {buttons.RESET: RED}
    assert lit("L") == {buttons.RESET: RED}      # a key this keypad has no light for: still cancellable
