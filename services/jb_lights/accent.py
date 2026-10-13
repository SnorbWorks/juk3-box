"""The accent chain as built: one WS2812B chain on GPIO13 (accent port J4).

    index 0-188    the upper panel: a freeform layout (top bar, columns of
                   different heights, a circle, two swooshes); positions are
                   camera-mapped in upper_panel_map.json (tools/ledmap)
    index 189-348  the footlights: 4 rows of 40, facing down onto the front
                   grill, serpentine, front row first:
                     row 0 (front)  189-228 left -> right
                     row 1          229-268 right -> left
                     row 2          269-308 left -> right
                     row 3 (back)   309-348 right -> left
                   (left/right as seen standing in front of the cabinet)

Effects work in space, not chain order: positions() gives every pixel an
(x, y) in one picture of the cabinet -- x 0..1 left to right across the upper
panel, y from 0 at its top to 1 at its bottom, carrying on down the cabinet
at the same scale: the level bar (matrix.positions) mid-height, the
footlights' rows in the lower third (front row nearest). part() names which panel a pixel is on.
"""

import json
from pathlib import Path

UPPER = 189
FOOT_ROWS, FOOT_COLS = 4, 40
FOOT = FOOT_ROWS * FOOT_COLS
NUM_PIXELS = UPPER + FOOT  # 349
GPIO = 13
MAX_AMPS = 6.0  # accent branch software cap (F3 is an 8A PTC)

_MAP = json.loads((Path(__file__).with_name("upper_panel_map.json")).read_text())
UPPER_ASPECT = _MAP["aspect"]  # width / height of the upper panel's LED extent

# The cabinet in inches (user's measurements, 2026-10-13): ~40" tall, the upper
# panel ~22" wide at the top, the level bar mid-height (matrix.positions), the
# footlights lighting the lower third. One inch in the shared picture:
UPPER_WIDTH_IN = 22.0
IN_X = 1 / UPPER_WIDTH_IN
IN_Y = UPPER_ASPECT / UPPER_WIDTH_IN

# Where the footlight rows sit in the shared picture: where their light lands,
# the lower third of the cabinet (~27" down). Rows are front to back, close together.
FOOT_Y0, FOOT_DY = 27.0 * IN_Y, 0.04


def foot_index(row: int, col: int) -> int:
    """Chain index of footlight (row 0 front .. 3 back, col 0 left .. 39 right)."""
    c = col if row % 2 == 0 else FOOT_COLS - 1 - col
    return UPPER + row * FOOT_COLS + c


def positions() -> list:
    """(x, y) for every chain index 0..348, in the shared cabinet picture."""
    pos = [None] * NUM_PIXELS
    for n, x, y in _MAP["points"]:
        pos[n - 1] = (x, y)
    for row in range(FOOT_ROWS):
        for col in range(FOOT_COLS):
            pos[foot_index(row, col)] = ((col + 0.5) / FOOT_COLS, FOOT_Y0 + row * FOOT_DY)
    return pos


def part(index: int) -> str:
    return "upper" if index < UPPER else "footlights"
