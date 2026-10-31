"""What's wired: the cabinet's chains and the fixtures on them, from one file.

    services/jb_lights/fixtures/juk3box-cabinet.json

A chain is one data line (a driver, a pixel count, a colour order, a current
cap). A fixture is a run of a chain's pixels that's one thing in the cabinet
-- the upper panel, the footlights -- with a role (what shows use it for)
and a layout (where each pixel is):

  map     camera-mapped points, [n, x, y] across the fixture's own extent
          (tools/ledmap), placed at at_in and width_in wide
  grid    rows x cols evenly spaced: at_in is the first pixel's centre (the
          "first" corner), pitch_in the spacing; order "rows" or "columns"
          is how the chain runs, serpentine if it turns back each line
  keypad  the keypad: the keys know their own places (show/parts.Keys)

Positions are in the shared cabinet picture every show draws in
(accent.positions): x 0..1 across the upper panel, y in the same units down
the cabinet -- picture.width_in inches across, picture.aspect wide to tall.

This release reads only the built-in file; the owner's own (a wizard for new
panels and strips) is later (docs/contracts/lights.md, Fixtures).
"""

import json
import re
from pathlib import Path

DIR = Path(__file__).with_name("fixtures")
BUILTIN = DIR / "juk3box-cabinet.json"
FORMAT = 1
ROLES = ("storyteller", "rhythm", "meter", "keys")
DRIVERS = ("pio", "spi", "pwm")
ORDERS = ("GRB", "RGB", "GRBW", "RGBW")
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
MAX_PIXELS = 2000
HARD_MAX_AMPS = 10.0  # no chain's software cap above this, whatever a file says
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _pair(v):
    return isinstance(v, list) and len(v) == 2 and all(_num(a) for a in v)


def _check_layout(where, lay, count, base: Path, probs):
    kind = lay.get("kind") if isinstance(lay, dict) else None
    if kind == "map":
        f = lay.get("file")
        if not isinstance(f, str) or "/" in f or not f.endswith(".json"):
            probs.append(f"{where}: map file must be a .json file next to the fixtures file")
            return
        try:
            m = json.loads((base / f).read_text())
            pts = m["points"]
            assert _num(m["aspect"]) and m["aspect"] > 0
            assert sorted(p[0] for p in pts) == list(range(1, count + 1))
            assert all(len(p) == 3 and _num(p[1]) and _num(p[2]) for p in pts)
        except (OSError, ValueError, KeyError, TypeError, AssertionError, IndexError):
            probs.append(f"{where}: map {f} must have an aspect and points [n, x, y] for n 1..{count}")
        if not _pair(lay.get("at_in")) or not (_num(lay.get("width_in")) and lay["width_in"] > 0):
            probs.append(f"{where}: a map needs at_in [x, y] and width_in")
    elif kind == "grid":
        rows, cols = lay.get("rows"), lay.get("cols")
        if not (_int(rows) and _int(cols) and rows > 0 and cols > 0 and rows * cols == count):
            probs.append(f"{where}: rows x cols must be the fixture's count ({count})")
        if lay.get("order") not in ("rows", "columns"):
            probs.append(f"{where}: order must be rows or columns")
        if not isinstance(lay.get("serpentine"), bool):
            probs.append(f"{where}: serpentine must be true or false")
        if lay.get("first") not in CORNERS:
            probs.append(f"{where}: first must be one of {', '.join(CORNERS)}")
        if not _pair(lay.get("at_in")) or not _pair(lay.get("pitch_in")):
            probs.append(f"{where}: a grid needs at_in [x, y] and pitch_in [x, y]")
    elif kind != "keypad":
        probs.append(f"{where}: layout kind must be map, grid or keypad")


def check(d, base: Path = DIR) -> list[str]:
    """What's wrong with a fixtures file, in words; [] if nothing."""
    if not isinstance(d, dict):
        return ["it isn't a fixtures file (a JSON object)"]
    if d.get("format") != FORMAT:
        return [f"it's fixtures format {d.get('format')!r}; this cabinet reads format {FORMAT}"]
    probs = []
    pic = d.get("picture")
    if not (isinstance(pic, dict) and _num(pic.get("width_in")) and pic["width_in"] > 0
            and _num(pic.get("aspect")) and pic["aspect"] > 0):
        probs.append("picture needs width_in and aspect")
    chains = d.get("chains")
    if not isinstance(chains, list) or not chains:
        return probs + ["chains must list at least one chain"]
    seen = set()
    for c in chains:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not ID_RE.match(c["id"]):
            probs.append("every chain needs an id (lowercase, dashes)")
            continue
        cid = c["id"]
        if cid in seen:
            probs.append(f"{cid}: two chains with that id")
        seen.add(cid)
        n = c.get("pixels")
        if not (_int(n) and 0 < n <= MAX_PIXELS):
            probs.append(f"{cid}: pixels must be 1..{MAX_PIXELS}")
            continue
        if c.get("driver") not in DRIVERS:
            probs.append(f"{cid}: driver must be one of {', '.join(DRIVERS)}")
        if c.get("order") not in ORDERS:
            probs.append(f"{cid}: order must be one of {', '.join(ORDERS)}")
        if not (_num(c.get("max_amps")) and 0 < c["max_amps"] <= HARD_MAX_AMPS):
            probs.append(f"{cid}: max_amps must be above 0 and at most {HARD_MAX_AMPS:g}")
        taken = [None] * n
        for fx in c.get("fixtures") or []:
            fid = fx.get("id") if isinstance(fx, dict) else None
            if not isinstance(fid, str) or not ID_RE.match(fid):
                probs.append(f"{cid}: every fixture needs an id (lowercase, dashes)")
                continue
            where = f"{cid}/{fid}"
            if fx.get("role") not in ROLES:
                probs.append(f"{where}: role must be one of {', '.join(ROLES)}")
            s, k = fx.get("start"), fx.get("count")
            if not (_int(s) and _int(k) and s >= 0 and k > 0 and s + k <= n):
                probs.append(f"{where}: start and count must lie inside the chain's {n} pixels")
                continue
            clash = {taken[i] for i in range(s, s + k) if taken[i]}
            if clash:
                probs.append(f"{where}: overlaps {', '.join(sorted(clash))}")
            taken[s:s + k] = [fid] * k
            _check_layout(where, fx.get("layout"), k, base, probs)
    return probs


class Cabinet:
    """A checked fixtures file: chains by id, and every pixel's place."""

    def __init__(self, d: dict, base: Path = DIR):
        self.raw, self.base = d, base
        self.width_in = float(d["picture"]["width_in"])
        self.aspect = float(d["picture"]["aspect"])
        self.chains = {c["id"]: c for c in d["chains"]}

    def to_picture(self, x_in: float, y_in: float) -> tuple:
        """Inches from the top-left of the upper panel -> the shared picture."""
        return x_in / self.width_in, y_in * self.aspect / self.width_in

    def max_amps(self, chain: str) -> float:
        return float(self.chains[chain]["max_amps"])

    def pixels(self, chain: str) -> int:
        return self.chains[chain]["pixels"]

    def fixture(self, chain: str, fid: str) -> dict:
        return next(f for f in self.chains[chain]["fixtures"] if f["id"] == fid)

    def by_role(self, role: str) -> list:
        return [(c["id"], f) for c in self.chains.values() for f in c["fixtures"] if f["role"] == role]

    def _place(self, fx: dict) -> dict:
        """{chain index: (x, y)} for a fixture's pixels."""
        lay, s = fx["layout"], fx["start"]
        out = {}
        if lay["kind"] == "map":
            m = json.loads((self.base / lay["file"]).read_text())
            w = float(lay["width_in"])
            h = w / m["aspect"]
            ax, ay = lay["at_in"]
            for n, x, y in m["points"]:
                out[s + n - 1] = self.to_picture(ax + x * w, ay + y * h)
        elif lay["kind"] == "grid":
            rows, cols = lay["rows"], lay["cols"]
            ax, ay = lay["at_in"]
            px, py = lay["pitch_in"]
            right, bottom = "right" in lay["first"], "bottom" in lay["first"]
            lines, along = (rows, cols) if lay["order"] == "rows" else (cols, rows)
            for line in range(lines):
                for k in range(along):
                    step = along - 1 - k if lay["serpentine"] and line % 2 else k
                    r, c = (line, step) if lay["order"] == "rows" else (step, line)
                    # r, c count from the first corner; the picture's x, y from the top-left
                    x = ax + (-c if right else c) * px
                    y = ay + (-r if bottom else r) * py
                    out[s + line * along + k] = self.to_picture(x, y)
        return out

    def positions(self, chain: str) -> list:
        """(x, y) for every pixel of a chain (None where nothing is placed)."""
        pos = [None] * self.pixels(chain)
        for fx in self.chains[chain]["fixtures"]:
            for i, p in self._place(fx).items():
                pos[i] = p
        return pos


def load(path: Path = BUILTIN) -> tuple:
    """(Cabinet or None, problems)."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        return None, [f"can't read {Path(path).name}: {e}"]
    probs = check(d, Path(path).parent)
    return (None, probs) if probs else (Cabinet(d, Path(path).parent), [])


def _builtin() -> Cabinet:
    cab, probs = load()
    if probs:
        raise ValueError(f"{BUILTIN.name}: {'; '.join(probs)}")
    return cab


CABINET = _builtin()
