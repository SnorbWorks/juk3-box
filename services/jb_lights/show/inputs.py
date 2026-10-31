"""What modules add to the lights: signals, cues and overlays from the bus
(docs/contracts/lights.md, Inputs from modules).

    jukebox/<id>/lights/signal           retained: palette, intensity
    jukebox/<id>/lights/cue              event: a named one-off
    jukebox/<id>/lights/overlay/<name>   retained: part of the cabinet for a while

The broker lets an installed module publish only under its own id, so the
topic says who sent it. Each message is checked against what the module's
manifest asked for ("lights": {"signal", "cues", "overlays"}), approved
by the owner at install; anything else is dropped, with the reason kept
for admin › Light Show › Inputs. The owner's strength for the module
(owner.input_gain) is applied when the layers draw it.

One Inputs is shared by every chain's loop (main.py); it's thread-safe.
"""

import json
import re
import threading
import time
from pathlib import Path

from services.jb_lights import fixtures
from services.jb_lights.show.library import MODULES_DIR

TOPIC = "jukebox/+/lights/#"
TOPIC_RE = re.compile(r"^jukebox/([a-z0-9]+(?:-[a-z0-9]+)*)/lights/(signal|cue|overlay/([a-z0-9]+(?:-[a-z0-9]+)*))$")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
OVERLAY_KINDS = {"fill", "bar", "pulse", "sparkle"}
MAX_TTL_S = 600
MAX_PALETTE = 8
MAX_OVERLAYS = 8          # per module
CUE_QUEUE = 16            # cues waiting to be drawn, all modules
MANIFEST_REFRESH_S = 5.0


def hex_rgb(h: str) -> tuple:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def targets(cabinet=None) -> dict[str, str]:
    """What an overlay can be on: {a role or a fixture's id: its role}."""
    cabinet = cabinet or fixtures.CABINET
    return {r: r for r in fixtures.ROLES} | {f["id"]: f["role"] for c in cabinet.chains.values() for f in c["fixtures"]}


def asked(manifest: dict) -> dict:
    """What a manifest's "lights" asks for, cleaned: {signal, cues, overlays}."""
    lights = manifest.get("lights") if isinstance(manifest, dict) else None
    lights = lights if isinstance(lights, dict) else {}
    cues = lights.get("cues") if isinstance(lights.get("cues"), list) else []
    overs = lights.get("overlays") if isinstance(lights.get("overlays"), list) else []
    return {"signal": lights.get("signal") is True,
            "cues": {c for c in cues if isinstance(c, str) and NAME_RE.match(c)},
            "overlays": {o for o in overs if isinstance(o, str)}}


def check_manifest_lights(lights, valid_targets=None) -> list[str]:
    """What's wrong with a manifest's "lights" (for module_mgr.check_manifest)."""
    if lights is None:
        return []
    if not isinstance(lights, dict) or set(lights) - {"signal", "cues", "overlays"}:
        return ['"lights" must be {"signal": true/false, "cues": [names], "overlays": [roles or fixtures]}']
    p = []
    if "signal" in lights and not isinstance(lights["signal"], bool):
        p.append("lights.signal must be true or false")
    cues = lights.get("cues", [])
    if not isinstance(cues, list) or not all(isinstance(c, str) and NAME_RE.match(c) for c in cues) or len(cues) > 20:
        p.append("lights.cues must list up to 20 cue names (lowercase, dashes)")
    overs = lights.get("overlays", [])
    ok = valid_targets or targets()
    if not isinstance(overs, list) or not all(isinstance(o, str) and o in ok for o in overs):
        p.append(f"lights.overlays must list roles or fixtures: {', '.join(sorted(ok))}")
    return p


def review_lights(lights) -> list[str]:
    """The lights a module asks for, in plain words (module_mgr.review)."""
    a = asked({"lights": lights})
    out = []
    if a["signal"]:
        out.append("Tints the light show with its own colours")
    if a["cues"]:
        out.append("Sets off light effects: " + ", ".join(sorted(a["cues"])))
    if a["overlays"]:
        names = {"storyteller": "the upper panel", "rhythm": "the footlights", "meter": "the level bar",
                 "keys": "the keypad (never while you're choosing a song)"}
        out.append("Can take over " + ", ".join(names.get(o, o) for o in sorted(a["overlays"])) + " for a while")
    return out


def parse(kind: str, data, allowed: dict, valid_targets=None):
    """(clean payload, None) or (None, why it's dropped)."""
    if not isinstance(data, dict):
        return None, "not a JSON object"
    if data.get("v", 1) != 1:
        return None, f"version {data.get('v')!r}, this jukebox reads 1"
    if kind == "signal":
        if not allowed["signal"]:
            return None, "its manifest doesn't ask for a signal"
        out = {}
        pal = data.get("palette")
        if pal is not None:
            if not isinstance(pal, list) or not 1 <= len(pal) <= MAX_PALETTE or not all(isinstance(c, str) and HEX_RE.match(c) for c in pal):
                return None, f"palette must be 1 to {MAX_PALETTE} colours like #ff8800"
            out["palette"] = [hex_rgb(c) for c in pal]
        if "intensity" in data:
            if not _num(data["intensity"]):
                return None, "intensity must be a number 0 to 1"
            out["intensity"] = max(0.0, min(1.0, float(data["intensity"])))
        return out, None
    if kind == "cue":
        cue = data.get("cue")
        if not isinstance(cue, str) or not NAME_RE.match(cue):
            return None, "cue must be a name (lowercase, dashes)"
        if cue not in allowed["cues"]:
            return None, f"its manifest doesn't ask for the {cue!r} cue"
        strength = data.get("strength", 1.0)
        color = data.get("color")
        if not _num(strength) or (color is not None and not (isinstance(color, str) and HEX_RE.match(color))):
            return None, "strength must be 0 to 1 and color like #ff8800"
        return {"cue": cue, "strength": max(0.0, min(1.0, float(strength))),
                "color": hex_rgb(color) if color else None}, None
    # an overlay
    on = data.get("on")
    valid_targets = valid_targets or targets()
    if on not in valid_targets:
        return None, f"overlay on {on!r}: not a role or fixture of this cabinet"
    if on not in allowed["overlays"] and valid_targets[on] not in allowed["overlays"]:   # a role covers its fixtures
        return None, f"its manifest doesn't ask for overlays on {on!r}"
    ttl, prio, look = data.get("ttl"), data.get("priority", 1), data.get("look")
    if not _num(ttl) or not 0 < ttl <= MAX_TTL_S:
        return None, f"an overlay needs a ttl of 1 to {MAX_TTL_S} seconds"
    if not (isinstance(prio, int) and not isinstance(prio, bool) and 1 <= prio <= 9):
        return None, "priority must be 1 to 9"
    if not isinstance(look, dict) or look.get("kind") not in OVERLAY_KINDS:
        return None, f"look.kind must be one of {', '.join(sorted(OVERLAY_KINDS))}"
    color = look.get("color", "#ffffff")
    if not (isinstance(color, str) and HEX_RE.match(color)):
        return None, "look.color must be like #ff8800"
    level, speed = look.get("level", 1.0), look.get("speed", 1.0)
    if not _num(level) or not _num(speed):
        return None, "look.level and look.speed must be numbers"
    return {"on": on, "priority": prio, "ttl": float(ttl),
            "look": {"kind": look["kind"], "color": hex_rgb(color),
                     "level": max(0.0, min(1.0, float(level))), "speed": max(0.2, min(4.0, float(speed)))}}, None


class Inputs:
    """Every module's current lights input. on_message() takes bus messages;
    the layers read state() each frame."""

    def __init__(self, modules_dir: Path = MODULES_DIR, manifests: dict | None = None, cabinet=None):
        self.modules_dir = Path(modules_dir)
        self.fixed = manifests                      # tests: {module id: manifest}
        self.valid_targets = targets(cabinet)
        self._lock = threading.Lock()
        self._allowed: dict[str, dict] = {}
        self._checked = -1e9
        self.signals: dict[str, dict] = {}          # module -> {palette, intensity, at}
        self.cues: list[dict] = []                  # {module, cue, strength, color, at}
        self.overlays: dict[tuple, dict] = {}       # (module, name) -> overlay + {until, at}
        self.dropped: dict[str, str] = {}           # module -> the last reason
        self.seen: dict[str, float] = {}            # module -> when it last sent something usable
        self._client = None

    # what each module may do -----------------------------------------------

    def allowed(self, module: str, now: float) -> dict | None:
        if now - self._checked >= MANIFEST_REFRESH_S:
            self._checked = now
            found = {}
            if self.fixed is not None:
                found = {m: asked(v) for m, v in self.fixed.items()}
            else:
                for f in self.modules_dir.glob("*/module.json") if self.modules_dir.is_dir() else []:
                    try:
                        m = json.loads(f.read_text())
                    except (OSError, ValueError):
                        continue
                    if isinstance(m, dict) and m.get("id") == f.parent.name:
                        found[f.parent.name] = asked(m)
            self._allowed = found
        return self._allowed.get(module)

    # from the bus ------------------------------------------------------------

    def on_message(self, topic: str, payload: bytes, now: float | None = None):
        now = time.monotonic() if now is None else now
        m = TOPIC_RE.match(topic)
        if not m:
            return
        module, kind, name = m.group(1), m.group(2), m.group(3)
        kind = "overlay" if name else kind
        with self._lock:
            allowed = self.allowed(module, now)
            if allowed is None:
                self.dropped[module] = "not an installed module"
                return
            if not payload:                         # an empty retained payload clears it
                if kind == "signal":
                    self.signals.pop(module, None)
                elif kind == "overlay":
                    self.overlays.pop((module, name), None)
                return
            try:
                data = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                self.dropped[module] = "not valid JSON"
                return
            clean, why = parse(kind, data, allowed, self.valid_targets)
            if why:
                self.dropped[module] = why
                return
            self.seen[module] = now
            if kind == "signal":
                self.signals[module] = {**clean, "at": now}
            elif kind == "cue":
                self.cues = [c for c in self.cues if now - c["at"] < 2.0][-(CUE_QUEUE - 1):]
                self.cues.append({**clean, "module": module, "at": now})
            else:
                mine = [k for k in self.overlays if k[0] == module and k != (module, name)]
                if len(mine) >= MAX_OVERLAYS:
                    self.dropped[module] = f"more than {MAX_OVERLAYS} overlays at once"
                    return
                self.overlays[(module, name)] = {**clean, "module": module, "name": name,
                                                 "at": now, "until": now + clean["ttl"]}

    def state(self, now: float | None = None) -> dict:
        """{signals, cues, overlays} as they stand now (expired overlays gone)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            for k in [k for k, o in self.overlays.items() if o["until"] <= now]:
                del self.overlays[k]
            return {"signals": dict(self.signals), "cues": list(self.cues),
                    "overlays": sorted(self.overlays.values(), key=lambda o: (o["priority"], o["at"]))}

    def report(self, now: float | None = None) -> dict:
        """For admin › Light Show › Inputs: what each module is doing."""
        now = time.monotonic() if now is None else now
        st = self.state(now)
        mods = set(self.seen) | set(self.dropped) | set(st["signals"])
        return {m: {"signal": m in st["signals"],
                    "overlays": [o["name"] for o in st["overlays"] if o["module"] == m],
                    "last_s": round(now - self.seen[m], 1) if m in self.seen else None,
                    "dropped": self.dropped.get(m)} for m in sorted(mods)}

    # the bus connection --------------------------------------------------------

    def start(self, host="localhost"):
        import paho.mqtt.client as mqtt
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = lambda c, u, f, rc, p=None: c.subscribe(TOPIC)

        def got(c, u, msg):
            try:
                self.on_message(msg.topic, msg.payload)
            except Exception:                       # never kill paho's thread
                pass
        self._client.on_message = got
        self._client.connect_async(host, 1883)
        self._client.loop_start()
        return self

    def stop(self):
        if self._client:
            self._client.loop_stop()
