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


def test_city_footlights_reflect_the_panel_and_trace_the_cars():
    from services.jb_lights.themes.accent_city import City
    city = City(accent.positions())
    frame = None
    for k in range(40):
        frame = city.frame(k / 40, 1 / 40)
    foot = frame[accent.UPPER:]
    assert all(b > 0 for _, _, b in foot)                       # night blue everywhere
    assert any(r > b for r, _, b in foot)                       # warm window light somewhere
    for row in range(1, accent.FOOT_ROWS):                      # all four rows show the same street
        for col in range(accent.FOOT_COLS):
            assert frame[accent.foot_index(row, col)] == frame[accent.foot_index(0, col)]
    street = [c for c in city.cars if c[0] in city.streets]
    assert street, "seed 11 starts with cars on the streets"
    lane, p, _, colour = street[0]
    x = accent.positions()[lane[int(p)]][0]
    col = min(accent.FOOT_COLS - 1, int(x * accent.FOOT_COLS))
    city.cars = []
    bare = city.frame(1.0, 0.0)[accent.foot_index(0, col)]
    assert sum(frame[accent.foot_index(0, col)]) > sum(bare)    # the car lit its spot


def test_level_bar_sits_mid_cabinet_left_aligned_with_the_panel():
    from services.jb_lights import matrix
    bar, pos = matrix.positions(), accent.positions()
    assert len(bar) == matrix.NUM_PIXELS and all(p is not None for p in bar)
    panel_bottom = max(y for _, y in pos[:accent.UPPER])
    foot_top = min(y for _, y in pos[accent.UPPER:])
    assert all(panel_bottom < y < foot_top for _, y in bar)
    xs = [x for x, _ in bar]
    assert min(xs) < 0.02 and 0.38 < max(xs) < 0.42            # ~9" of the panel's 22"
    left, right = bar[matrix.index(0, 0)], bar[matrix.index(35, 0)]
    assert left[0] < right[0]
    assert bar[matrix.index(0, 4)][1] < bar[matrix.index(0, 0)][1]   # row 0 is the bottom
