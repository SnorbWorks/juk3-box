"""admin › Light Show (docs/contracts/lights.md): the show library, the
owner's light settings, uploaded shows, modules' inputs, the hardware, and
previews rendered by the engine's own code.

The admin writes; jb-lights reads (show/library.py picks up changes within
a couple of seconds, no restart). What's playing comes back from jb-lights
on the bus (jukebox/jb-lights/state, retained).
"""

import json
from pathlib import Path

from services.jb_lights import fixtures, owner, present
from services.jb_lights.show import inputs as light_inputs
from services.jb_lights.show import library as light_library
from services.jb_lights.show import styles

MAX_SHOW_BYTES = 64 * 1024
PREVIEW_CACHE = 4


class LightsError(Exception):
    pass


class LightsAdmin:
    def __init__(self, lights_dir: Path, modules_dir: Path, lights_path: Path):
        self.dir = Path(lights_dir)
        self.settings_path = self.dir / "settings.json"
        self.shows_dir = self.dir / "shows"
        self.modules_dir = Path(modules_dir)
        self.lights_path = Path(lights_path)        # which chains are wired (the setup wizard's)
        self._previews: dict = {}

    def library(self) -> light_library.Library:
        return light_library.Library(settings_path=self.settings_path, shows_dir=self.shows_dir,
                                     modules_dir=self.modules_dir)

    # Reading -----------------------------------------------------------------

    def view(self, live: dict | None) -> dict:
        lib = self.library()
        shows = [{"id": sid, "name": st.title or sid, "description": st.description, "for": list(st.groups),
                  "source": st.source, "version": st.version, "author": st.author,
                  "palette": ["#%02x%02x%02x" % tuple(c) for c in (st.palettes[0] if st.palettes else [])][:6],
                  "disabled": sid in lib.settings["disabled"]}
                 for sid, st in sorted(lib.shows.items(), key=lambda kv: (kv[1].source != "builtin", kv[0]))]
        groups = [{"id": g, "name": styles.GROUP_NAMES.get(g, g), "rotation": lib.rotation(g),
                   "custom": g in lib.settings["rotations"]} for g in styles.GROUPS]
        return {"now": live, "shows": shows, "groups": groups, "settings": lib.settings, "notes": lib.notes,
                "inputs": self.inputs(lib.settings, (live or {}).get("inputs") or {}),
                "hardware": self.hardware()}

    def inputs(self, settings: dict, report: dict) -> list:
        """Every installed module that asks for lights, the owner's setting
        for it, and what jb-lights last heard from it."""
        out = []
        for f in sorted(self.modules_dir.glob("*/module.json")) if self.modules_dir.is_dir() else []:
            try:
                m = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(m, dict) or not m.get("lights"):
                continue
            mid = f.parent.name
            v = settings["inputs"].get(mid, {"enabled": True, "strength": 1.0})
            out.append({"id": mid, "name": m.get("name") or mid, "asks": light_inputs.review_lights(m["lights"]),
                        "enabled": v["enabled"], "strength": v["strength"], "heard": report.get(mid)})
        return out

    def hardware(self) -> dict:
        wired = present.read(self.lights_path)
        chains = []
        for cid, c in fixtures.CABINET.chains.items():
            chains.append({"id": cid, "name": c.get("name", cid), "driver": c["driver"], "gpio": c.get("gpio"),
                           "pixels": c["pixels"], "order": c["order"], "max_amps": c["max_amps"],
                           "wired": bool(wired.get(cid)),
                           "fixtures": [{"id": f["id"], "name": f.get("name", f["id"]), "role": f["role"],
                                         "count": f["count"], "layout": f["layout"]["kind"]} for f in c["fixtures"]]})
        return {"name": fixtures.CABINET.raw.get("name"), "chains": chains}

    def show_file(self, sid: str) -> dict:
        lib = self.library()
        st = lib.shows.get(sid)
        if st is None:
            raise LightsError("There's no show with that id.")
        return st.raw

    # Changing ----------------------------------------------------------------

    def save_settings(self, change: dict) -> dict:
        """Merges a change into the owner's settings (checked: anything out of
        range is pulled in; unknown shows are refused, with a reason)."""
        if not isinstance(change, dict):
            raise LightsError("Send the settings as an object.")
        lib = self.library()
        cur = dict(lib.settings)
        for key in ("pin", "idle"):
            if key in change:
                v = change[key]
                ok = v is None if key == "pin" else v == "city"
                if not ok and v not in lib.shows:
                    raise LightsError(f"There's no show called {v!r}.")
        if "rotations" in change and isinstance(change["rotations"], dict):
            for g, ids in change["rotations"].items():
                if g not in styles.GROUPS:
                    raise LightsError(f"There's no genre group called {g!r}.")
                if ids is not None and (not isinstance(ids, list) or any(i not in lib.shows for i in ids)):
                    raise LightsError("A rotation can only list shows that are here.")
            rot = {**cur["rotations"], **change["rotations"]}
            change = {**change, "rotations": {g: v for g, v in rot.items() if v is not None}}   # null: back to every show for it
        if "inputs" in change and isinstance(change["inputs"], dict):
            change = {**change, "inputs": {**cur["inputs"], **change["inputs"]}}
        if "quiet" in change and isinstance(change["quiet"], dict):
            change = {**change, "quiet": {**cur["quiet"], **change["quiet"]}}
        new = owner.clean({**cur, **change})
        self._write(self.settings_path, new)
        return new

    def upload(self, data: bytes, replace: bool = False) -> dict:
        if len(data) > MAX_SHOW_BYTES:
            raise LightsError(f"A show file is at most {MAX_SHOW_BYTES // 1024} KB.")
        try:
            d = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise LightsError("That isn't a show file (it isn't JSON).")
        problems = styles.check(d)
        if problems:
            raise LightsError("That show has problems: " + "; ".join(problems))
        lib = self.library()
        sid = d["id"]
        there = lib.shows.get(sid)
        if there is not None and there.source != "uploaded":
            raise LightsError(f"\"{sid}\" is one of the {'built-in' if there.source == 'builtin' else there.source} shows; give yours another id.")
        if there is not None and not replace:
            raise LightsError(f"There's already an uploaded show called \"{sid}\". Upload it again to replace it.")
        self.shows_dir.mkdir(parents=True, exist_ok=True)
        self._write(self.shows_dir / f"{sid}.json", d)
        return {"id": sid, "name": d.get("name", sid), "replaced": there is not None}

    def delete(self, sid: str) -> None:
        lib = self.library()
        st = lib.shows.get(sid)
        if st is None or st.source != "uploaded":
            raise LightsError("Only shows you uploaded can be deleted.")
        for f in self.shows_dir.glob("*.json"):
            try:
                if json.loads(f.read_text()).get("id") == sid:
                    f.unlink()
            except (OSError, ValueError):
                continue
        s = lib.settings
        change = {}
        if s["pin"] == sid:
            change["pin"] = None
        if s["idle"] == sid:
            change["idle"] = "city"
        rot = {g: [i for i in ids if i != sid] for g, ids in s["rotations"].items()}
        if rot != s["rotations"]:
            change["rotations"] = rot
        if change:
            self._write(self.settings_path, owner.clean({**s, **change}))

    def preview(self, sid: str) -> dict:
        from services.jb_lights import preview         # the engine: only when asked
        lib = self.library()
        st = lib.shows.get(sid)
        if st is None:
            raise LightsError("There's no show with that id.")
        key = (sid, st.source, json.dumps(st.raw, sort_keys=True))
        if key not in self._previews:
            if len(self._previews) >= PREVIEW_CACHE:
                self._previews.pop(next(iter(self._previews)))
            self._previews[key] = preview.render(st)
        return self._previews[key]

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, indent=1) + "\n")
        tmp.replace(path)
