import copy
import json
from pathlib import Path

from services.jb_lights import accent, fixtures, matrix

OLD = json.loads((Path(__file__).with_name("old_positions.json")).read_text())


def close(a, b, tol=1e-4):
    return len(a) == len(b) and all(abs(p[0] - q[0]) < tol and abs(p[1] - q[1]) < tol for p, q in zip(a, b))


def test_the_cabinet_file_puts_every_pixel_where_it_was():
    assert fixtures.check(json.loads(fixtures.BUILTIN.read_text())) == []
    assert close(accent.positions(), OLD["accent"])          # as hand-coded before the fixtures file
    assert close(matrix.positions(), OLD["bar"])
    assert accent.MAX_AMPS == 6.0 and matrix.MAX_AMPS == 4.0
    cab = fixtures.CABINET
    assert cab.pixels("accent") == accent.NUM_PIXELS and cab.pixels("levelbar") == matrix.NUM_PIXELS
    assert [f["id"] for _, f in cab.by_role("rhythm")] == ["footlights"]
    assert abs(accent.FOOT_DY - 0.04) < 1e-4


def test_a_grid_runs_the_way_its_wired():
    for row in range(accent.FOOT_ROWS):                      # serpentine, front row first
        xs = [accent.positions()[accent.foot_index(row, c)][0] for c in range(accent.FOOT_COLS)]
        assert xs == sorted(xs)
    bar = matrix.positions()
    assert bar[matrix.index(0, 0)][1] > bar[matrix.index(0, 4)][1]   # row 0 is the bottom


def test_a_bad_fixtures_file_says_what_is_wrong():
    good = json.loads(fixtures.BUILTIN.read_text())
    assert fixtures.check({**good, "format": 2})[0].startswith("it's fixtures format 2")
    d = copy.deepcopy(good)
    d["chains"][0]["max_amps"] = 40
    d["chains"][0]["fixtures"][1]["start"] = 200                # runs off the end of the chain
    d["chains"][1]["fixtures"][0]["layout"]["rows"] = 6
    d["chains"][2]["fixtures"][0]["role"] = "disco"
    text = " / ".join(fixtures.check(d))
    assert "max_amps" in text and "inside the chain" in text and "rows x cols" in text and "role" in text
    d = copy.deepcopy(good)
    d["chains"][0]["fixtures"][1].update(start=180, count=160, layout={"kind": "keypad"})
    assert "overlaps upper-panel" in " / ".join(fixtures.check(d))
    d = copy.deepcopy(good)
    d["chains"][0]["fixtures"][0]["layout"]["file"] = "../../etc/passwd.json"
    assert "map file" in " / ".join(fixtures.check(d))
