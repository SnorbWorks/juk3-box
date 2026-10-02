"""The level bar as actually built: a 36 x 5 matrix of WS2812B pixels.

36 vertical strips of 5 pixels each (not the plan's 32 x 6). Confirmed with an
orientation test: strip 1 is at the far left, every strip runs bottom -> top,
and all strips run the same direction (no serpentine wiring), so

    pixel index = column * ROWS + row      (column 0 = left, row 0 = bottom)
"""

COLS = 36
ROWS = 5
NUM_PIXELS = COLS * ROWS  # 180
MAX_AMPS = 4.0  # level-bar branch software cap (F4 is a 5A PTC)

BLACK = (0, 0, 0)


def index(col: int, row: int) -> int:
    return col * ROWS + row


def blank() -> list:
    return [BLACK] * NUM_PIXELS
