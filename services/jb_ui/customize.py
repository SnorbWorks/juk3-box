"""A theme's global options: colors, fonts, shape, background
(docs/theme-format.md, "Customize").

A theme declares its options and its styles (presets that set several
options at once) in theme.json under "customize". The owner picks a style
and may change single options on top of it; the engine sets each option as
a CSS custom property, --opt-<id>, which the theme's CSS reads.

Pure data handling, no web server, so it can be tested on its own.
"""

import json
import re
from pathlib import Path

from services.jb_ui import themes

TYPES = {"color", "range", "toggle", "font", "picture"}
GROUPS = ("colors", "fonts", "shape", "background", "motion")
COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")
MEDIA_RE = re.compile(r"^/media/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
ROLES = {"background"}

FONTS_DIR = Path(__file__).resolve().parent / "engine" / "fonts"
FONT_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(ttf|otf|woff2?)$")


def font_library() -> list[dict]:
    """Fonts the engine ships (engine/fonts/fonts.json), for any theme's
    font options: [{id, label, family, src, ...}]."""
    try:
        fonts = json.loads((FONTS_DIR / "fonts.json").read_text())
    except (OSError, ValueError):
        return []
    return [f for f in fonts if isinstance(f, dict) and themes.ID_RE.match(str(f.get("id", "")))
            and isinstance(f.get("family"), str) and FONT_FILE_RE.match(str(f.get("src", "")))
            and (FONTS_DIR / f["src"]).is_file()]


def options_of(manifest: dict) -> list[dict]:
    c = manifest.get("customize")
    return c.get("options", []) if isinstance(c, dict) else []


def styles_of(manifest: dict) -> list[dict]:
    c = manifest.get("customize")
    return c.get("styles", []) if isinstance(c, dict) else []


def check(manifest: dict, root: Path, problems: list[str]) -> None:
    """Adds what's wrong with a theme's "customize" block to `problems`."""
    c = manifest.get("customize")
    if c is None:
        return
    if not isinstance(c, dict):
        problems.append("customize must be an object")
        return
    options = c.get("options", [])
    if not isinstance(options, list):
        problems.append("customize.options must be a list")
        return
    seen: dict[str, dict] = {}
    fonts = {f["id"] for f in font_library()}
    for o in options:
        if not isinstance(o, dict) or not themes.ID_RE.match(str(o.get("id", ""))):
            problems.append("customize: every option needs an id like \"panel\"")
            continue
        oid = o["id"]
        if oid in seen:
            problems.append(f"customize: option {oid!r} is declared twice")
        if o.get("type") not in TYPES:
            problems.append(f"customize option {oid!r}: type must be one of {', '.join(sorted(TYPES))}")
            continue
        if not isinstance(o.get("label"), str) or not o["label"].strip():
            problems.append(f"customize option {oid!r} needs a label")
        if o.get("group", "colors") not in GROUPS:
            problems.append(f"customize option {oid!r}: group must be one of {', '.join(GROUPS)}")
        if o.get("role") is not None and o.get("role") not in ROLES:
            problems.append(f"customize option {oid!r}: role must be background")
        if o["type"] == "range":
            lo, hi, step = o.get("min"), o.get("max"), o.get("step")
            if not all(_number(v) for v in (lo, hi, step)) or not lo < hi or step <= 0:
                problems.append(f"customize option {oid!r}: a range needs numbers min < max and a step above 0")
                continue
        if not valid(o, o.get("default"), root, fonts):
            problems.append(f"customize option {oid!r}: its default {o.get('default')!r} isn't a usable {o['type']}")
        seen[oid] = o
    styles = c.get("styles", [])
    if not isinstance(styles, list):
        problems.append("customize.styles must be a list")
        return
    style_ids = set()
    for s in styles:
        if not isinstance(s, dict) or not themes.ID_RE.match(str(s.get("id", ""))) or not isinstance(s.get("label"), str):
            problems.append("customize: every style needs an id and a label")
            continue
        if s["id"] in style_ids:
            problems.append(f"customize: style {s['id']!r} is declared twice")
        style_ids.add(s["id"])
        values = s.get("values", {})
        if not isinstance(values, dict):
            problems.append(f"customize style {s['id']!r}: values must be an object")
            continue
        for k, v in values.items():
            if k not in seen:
                problems.append(f"customize style {s['id']!r} sets {k!r}, which isn't an option")
            elif not valid(seen[k], v, root, fonts):
                problems.append(f"customize style {s['id']!r}: {v!r} isn't a usable value for {k!r}")


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def valid(option: dict, value, root: Path | None, fonts: set[str] | None = None) -> bool:
    """Whether `value` is usable for `option`."""
    t = option.get("type")
    if t == "color":
        return isinstance(value, str) and bool(COLOR_RE.match(value))
    if t == "range":
        return _number(value) and option["min"] <= value <= option["max"]
    if t == "toggle":
        return isinstance(value, bool)
    if t == "font":
        if fonts is None:
            fonts = {f["id"] for f in font_library()}
        return value == "theme" or value in fonts
    if t == "picture":
        if value == "" or (isinstance(value, str) and MEDIA_RE.match(value) and ".." not in value):
            return True
        if root is None or not isinstance(value, str):
            return False
        path = themes.safe_path(root, value)
        return path is not None and path.is_file()
    return False


def resolve(manifest: dict, root: Path, owner: dict | None) -> tuple[dict, list[str]]:
    """What the engine applies: {"style", "values" (every option's value)},
    and problems with the owner's choices (each one is left at the style's
    or theme's value, so a bad save never breaks the screen)."""
    options = {o["id"]: o for o in options_of(manifest)}
    styles = {s["id"]: s for s in styles_of(manifest)}
    owner = owner if isinstance(owner, dict) else {}
    problems: list[str] = []
    fonts = {f["id"] for f in font_library()}

    style = owner.get("style")
    if style not in styles:
        if style is not None:
            problems.append(f"style {style!r} isn't one of this theme's")
        style = next(iter(styles), None)
    values = {oid: o.get("default") for oid, o in options.items()}
    if style:
        values.update(styles[style].get("values", {}))
    custom = owner.get("values")
    changed = []
    if isinstance(custom, dict):
        for k, v in custom.items():
            if k not in options:
                problems.append(f"{k!r} isn't an option of this theme")
            elif not valid(options[k], v, root, fonts):
                problems.append(f"{v!r} isn't a usable value for {k!r}")
            else:
                values[k] = v
                changed.append(k)
    return {"style": style, "values": values, "changed": changed}, problems


def clean(manifest: dict, root: Path, owner) -> tuple[dict, list[str]]:
    """The owner's choices as they'd be saved: a known style (or none) and
    only usable values that differ from the style. Problems name the rest."""
    options = {o["id"]: o for o in options_of(manifest)}
    styles = {s["id"]: s for s in styles_of(manifest)}
    if not isinstance(owner, dict):
        return {"style": None, "values": {}}, ["expected an object with a style and values"]
    problems: list[str] = []
    style = owner.get("style")
    if style is not None and style not in styles:
        problems.append(f"style {style!r} isn't one of this theme's")
        style = None
    base = {oid: o.get("default") for oid, o in options.items()}
    if style:
        base.update(styles[style].get("values", {}))
    values = {}
    fonts = {f["id"] for f in font_library()}
    raw = owner.get("values", {})
    if not isinstance(raw, dict):
        problems.append("values must be an object")
        raw = {}
    for k, v in raw.items():
        if k not in options:
            problems.append(f"{k!r} isn't an option of this theme")
        elif not valid(options[k], v, root, fonts):
            problems.append(f"{v!r} isn't a usable value for {options[k]['label']}")
        elif v != base.get(k):
            values[k] = v
    return {"style": style, "values": values}, problems
