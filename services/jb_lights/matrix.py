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

# Physical pitch (user, 2026-10-13): strips 1/4" apart, pixels ~1/2" apart up
# a strip -- each pixel is twice as tall as it is wide, so a round shape needs
# half as many rows as columns. The bar is ~9" x 2.5", mid-height on the
# cabinet, left-aligned with the upper panel.
COL_PITCH_IN, ROW_PITCH_IN = 0.25, 0.5
CENTRE_IN = 20.0  # bar centre, inches down from the top of the cabinet

BLACK = (0, 0, 0)


def index(col: int, row: int) -> int:
    return col * ROWS + row


def blank() -> list:
    return [BLACK] * NUM_PIXELS


def positions() -> list:
    """(x, y) for every bar pixel in the cabinet picture shared with the accent
    chain (accent.positions), so effects can run across the whole cabinet."""
    from services.jb_lights.accent import IN_X, IN_Y
    pos = [None] * NUM_PIXELS
    for col in range(COLS):
        for row in range(ROWS):
            x = (col + 0.5) * COL_PITCH_IN * IN_X
            y = (CENTRE_IN + ((ROWS - 1) / 2 - row) * ROW_PITCH_IN) * IN_Y
            pos[index(col, row)] = (x, y)
    return pos
