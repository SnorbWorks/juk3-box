from services.jb_lights import accent


def test_chain_sizes():
    assert accent.NUM_PIXELS == 349
    assert len(accent.positions()) == 349
    assert all(p is not None for p in accent.positions())


def test_footlights_are_serpentine_front_row_first():
    # Front row runs left to right from 189; the next row comes back.
    assert accent.foot_index(0, 0) == 189 and accent.foot_index(0, 39) == 228
    assert accent.foot_index(1, 39) == 229 and accent.foot_index(1, 0) == 268
    assert accent.foot_index(2, 0) == 269 and accent.foot_index(3, 0) == 348


def test_positions_share_one_picture():
    pos = accent.positions()
    upper, foot = pos[:189], pos[189:]
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in upper)
    assert all(y > 1 for _, y in foot)               # footlights sit below the upper panel
    assert pos[0][1] > 0.9 and pos[188][1] < 0.1      # pixel 1 bottom strip, 189 top bar
    assert pos[accent.foot_index(0, 0)][0] < pos[accent.foot_index(0, 39)][0]
