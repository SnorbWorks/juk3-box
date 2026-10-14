"""Blocks that modules add to the page builder (docs/contracts/extensions.md,
Blocks): markup and style files plus data off the bus. No module code runs
in the cabinet's page: the markup is sanitized like a theme's, and the
style is served inside an @scope of the module's own blocks, so it can't
reach anything else on the screen.
"""

import json
import re
from pathlib import Path

from services.jb_ui import icons, markup, themes

ICON_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TOPIC_RE = re.compile(r"^jukebox/[A-Za-z0-9_./-]+$")
ASSET_TYPES = {".css", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Settings a module's block may have in the page builder (the engine sets
# each as an attribute on the block's element, for its style to use).
BLOCK_SETTING_TYPES = {"bool", "enum", "text", "int"}


def manifests(folders: list[Path]) -> dict[str, tuple[dict, Path]]:
    """Every module's manifest, by id, with its folder (first found wins,
    so a built-in module can't be shadowed by an installed one)."""
    found: dict[str, tuple[dict, Path]] = {}
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*/module.json")):
            try:
                m = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"] not in found:
                found[m["id"]] = (m, path.parent)
    return found


def short_id(module_id: str) -> str:
    return module_id[3:] if module_id.startswith("jb-") else module_id


def check_block(module_id: str, root: Path, b) -> list[str]:
    """What's wrong with one block declaration (empty if it's usable)."""
    if not isinstance(b, dict):
        return ["a block must be an object"]
    problems = []
    name, short = str(b.get("id", "")), short_id(module_id)
    if not themes.ID_RE.match(name) or not (name == short or name.startswith(f"{short}-")):
        problems.append(f"block id {name!r} must be {short!r} or start with {short + '-'!r}")
    elif name in themes.KNOWN_BLOCKS:
        problems.append(f"block id {name!r} is one of the engine's")
    if b.get("kind", "display") not in ("display", "playlist"):
        problems.append(f"block {name!r}: kind must be display or playlist")
    if not isinstance(b.get("label"), str) or not b["label"].strip():
        problems.append(f"block {name!r} needs a label")
    if not isinstance(b.get("topic"), str) or not TOPIC_RE.match(b["topic"]):
        problems.append(f"block {name!r} needs a topic under jukebox/")
    for key, suffix in (("template", ".html"), ("style", ".css")):
        rel = b.get(key)
        if rel is None and key == "style":
            continue
        path = themes.safe_path(root, rel) if isinstance(rel, str) else None
        if path is None or path.suffix.lower() != suffix or not path.is_file():
            problems.append(f"block {name!r}: {key} must be a {suffix} file in the module")
    if not isinstance(b.get("icons", []), list) or not all(isinstance(i, str) and ICON_RE.match(i) for i in b.get("icons", [])):
        problems.append(f"block {name!r}: icons must be icon names")
    settings = b.get("settings", {})
    if not isinstance(settings, dict):
        problems.append(f"block {name!r}: settings must be an object")
    else:
        for key, spec in settings.items():
            if not themes.SETTING_RE.match(str(key)) or key in ("block", "class", "span", "shape"):
                problems.append(f"block {name!r}: {key!r} can't be a setting's name")
            elif not isinstance(spec, dict) or spec.get("type") not in BLOCK_SETTING_TYPES or not isinstance(spec.get("label"), str):
                problems.append(f"block {name!r}: setting {key!r} needs a label and a type ({', '.join(sorted(BLOCK_SETTING_TYPES))})")
    shapes = b.get("shapes", [])
    if not isinstance(shapes, list) or not all(
            isinstance(s, list) and len(s) == 2 and isinstance(s[0], str) and themes.ID_RE.match(s[0])
            and isinstance(s[1], (int, float)) for s in shapes):
        problems.append(f"block {name!r}: shapes must be [name, width] pairs")
    return problems


def module_blocks(folders: list[Path]) -> tuple[dict[str, dict], list[str]]:
    """Every usable module block, by name, as the engine needs it; problems
    for the ones skipped."""
    out: dict[str, dict] = {}
    problems: list[str] = []
    for mid, (m, root) in manifests(folders).items():
        for b in m.get("blocks") or []:
            found = check_block(mid, root, b)
            if found:
                problems.extend(f"module {mid}: {p}" for p in found)
                continue
            if b["id"] in out:
                problems.append(f"module {mid}: block {b['id']!r} is already another module's")
                continue
            base = f"/modules/{mid}/"
            out[b["id"]] = {
                "module": mid,
                "kind": b.get("kind", "display"),
                "label": b["label"],
                "description": b.get("description", ""),
                "group": b.get("group", "content"),
                "icon": b.get("icon", ""),
                "topic": b["topic"],
                "template": markup.sanitize((root / b["template"]).read_text(), base),
                "style": base + b["style"] if b.get("style") else None,
                "icons": {n: {"svg": s} for n in b.get("icons", []) if (s := icons.svg(f"lucide:{n}"))},
                "shapes": b.get("shapes", []),
                "settings": b.get("settings") or {},
            }
    return out, problems


def asset(folders: list[Path], module_id: str, rel: str) -> tuple[bytes | Path, str] | None:
    """A module's file for the screen: (path, content type), or (bytes, type)
    for a style sheet, which comes wrapped in @scope of the module's blocks.
    None for anything else."""
    found = manifests(folders).get(module_id)
    if not found:
        return None
    m, root = found
    path = themes.safe_path(root, rel)
    if path is None or not path.is_file() or path.suffix.lower() not in ASSET_TYPES:
        return None
    if path.suffix.lower() != ".css":
        return path, ""
    names = [str(b["id"]) for b in m.get("blocks") or [] if isinstance(b, dict) and themes.ID_RE.match(str(b.get("id", "")))]
    if not names:
        return None
    css = path.read_text(errors="replace")
    if not contained(css):
        return None
    scope = ", ".join(f"jb-{n}" for n in names)
    return f"@scope ({scope}) {{\n{css}\n}}\n".encode(), "text/css"


def contained(css: str) -> bool:
    """Whether a style sheet stays inside the @scope it's served in: its
    braces balance (never closing more than it opened, which would end the
    scope early) and it imports nothing."""
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    bare = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', bare)
    if "@import" in bare.lower() or "\\" in bare:
        return False
    depth = 0
    for ch in bare:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0
