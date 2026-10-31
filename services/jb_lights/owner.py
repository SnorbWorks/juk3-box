"""The owner's light settings (admin › Light Show), one file:

    /var/lib/juk3-box/lights/settings.json

  pin         a show for every song, or null to pick by genre
  rotations   {genre group: [show ids]}: the shows that take turns for that
              group's songs (a group not named here: every show made for it)
  disabled    show ids never picked
  idle        what the cabinet does with no music: "city" (the city and
              the waves) or an idle show's id
  brightness  0.1 .. 1, on top of every look and every input
  low_flash   true: white flashes become soft swells, and bursts are gentle
  quiet       {enabled, from, to, brightness}: dimmer at night (local time)
  inputs      {module id: {enabled, strength 0..1}}: how much each module's
              lights reach the cabinet (not named: on, full strength)

jb-admin writes it (through clean()), jb-lights reads it (read(), which
also cleans, so a hand-edited file can't push anything out of range).
"""

import json
import re
from pathlib import Path

DIR = Path("/var/lib/juk3-box/lights")
PATH = DIR / "settings.json"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

DEFAULTS = {
    "pin": None,
    "rotations": {},
    "disabled": [],
    "idle": "city",
    "brightness": 1.0,
    "low_flash": False,
    "quiet": {"enabled": False, "from": "22:00", "to": "07:00", "brightness": 0.3},
    "inputs": {},
}


def _num(v, lo, hi, default):
    return max(lo, min(hi, float(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _id(v):
    return v if isinstance(v, str) and ID_RE.match(v) and len(v) <= 40 else None


def clean(raw) -> dict:
    """The settings with every value checked and in range; anything unknown dropped."""
    raw = raw if isinstance(raw, dict) else {}
    out = json.loads(json.dumps(DEFAULTS))
    out["pin"] = _id(raw.get("pin"))
    rot = raw.get("rotations")
    if isinstance(rot, dict):
        out["rotations"] = {g: [i for i in ids if _id(i)][:20] for g, ids in rot.items()
                            if isinstance(g, str) and _id(g) and isinstance(ids, list)}
    if isinstance(raw.get("disabled"), list):
        out["disabled"] = [i for i in raw["disabled"] if _id(i)][:200]
    out["idle"] = _id(raw.get("idle")) or "city"
    out["brightness"] = _num(raw.get("brightness"), 0.1, 1.0, 1.0)
    out["low_flash"] = raw.get("low_flash") is True
    q = raw.get("quiet") if isinstance(raw.get("quiet"), dict) else {}
    out["quiet"] = {"enabled": q.get("enabled") is True,
                    "from": q["from"] if isinstance(q.get("from"), str) and TIME_RE.match(q["from"]) else "22:00",
                    "to": q["to"] if isinstance(q.get("to"), str) and TIME_RE.match(q["to"]) else "07:00",
                    "brightness": _num(q.get("brightness"), 0.05, 1.0, 0.3)}
    inputs = raw.get("inputs")
    if isinstance(inputs, dict):
        out["inputs"] = {m: {"enabled": v.get("enabled") is not False, "strength": _num(v.get("strength"), 0.0, 1.0, 1.0)}
                         for m, v in inputs.items() if _id(m) and isinstance(v, dict)}
    return out


def read(path: Path = PATH) -> dict:
    try:
        return clean(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        return clean({})


def input_gain(settings: dict, module: str) -> float:
    """How strongly a module's lights reach the cabinet: 0 (off) .. 1."""
    v = settings["inputs"].get(module)
    if v is None:
        return 1.0
    return v["strength"] if v["enabled"] else 0.0


def quiet_now(settings: dict, minutes: int) -> bool:
    """Is it quiet hours at this local time (minutes after midnight)?"""
    q = settings["quiet"]
    if not q["enabled"]:
        return False
    a = int(q["from"][:2]) * 60 + int(q["from"][3:])
    b = int(q["to"][:2]) * 60 + int(q["to"][3:])
    return a <= minutes < b if a <= b else minutes >= a or minutes < b
