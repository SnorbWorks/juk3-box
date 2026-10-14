"""Theme discovery, loading and validation (theme format 1).

Pure file handling, no web server, so it can be tested on its own. The
format itself is documented in docs/theme-format.md; this module is the
engine's side of that promise, so keep the two in step.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

FORMAT = 1

REQUIRED_LAYOUTS = ("frame", "home", "all-songs")

# Blocks this engine knows. A layout naming any other block still loads
# (a newer theme on an older engine), with a warning, and the block renders
# as nothing.
KNOWN_BLOCKS = {
    "screen", "logo", "nav", "now-playing", "up-next", "board",
    "section", "page-info", "pager", "header", "connection", "collection",
    "heading", "text", "image", "spacer", "shortcut",
}

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
CLASS_RE = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*( -?[A-Za-z_][A-Za-z0-9_-]*)*$")
SETTING_RE = re.compile(r"^[a-z][a-z0-9-]*$")
BACKGROUND_TYPES = {"image", "video", "none"}
SIZING_MODES = {"fluid", "canvas"}


class ThemeError(Exception):
    """A theme that can't be used. `problems` lists every reason found."""

    def __init__(self, theme_id: str, problems: list[str]):
        self.theme_id = theme_id
        self.problems = problems
        super().__init__(f"theme {theme_id!r}: " + "; ".join(problems))


@dataclass
class Theme:
    id: str
    root: Path
    manifest: dict
    layouts: dict[str, dict]
    warnings: list[str] = field(default_factory=list)


def find_theme_dir(theme_id: str, search_dirs: list[Path]) -> Path | None:
    """The folder for `theme_id`, searching `search_dirs` in order.

    Earlier directories win, so a theme you add can't shadow one that ships
    with JUKE.BOX under the same id.
    """
    if not ID_RE.match(theme_id):
        return None
    for base in search_dirs:
        candidate = base / theme_id
        if (candidate / "theme.json").is_file():
            return candidate
    return None


def list_themes(search_dirs: list[Path]) -> list[str]:
    seen: list[str] = []
    for base in search_dirs:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if (child / "theme.json").is_file() and child.name not in seen:
                seen.append(child.name)
    return seen


def safe_path(root: Path, relative: str) -> Path | None:
    """`relative` resolved inside `root`, or None if it points anywhere else.

    Rejects URLs, absolute paths and `..` escapes, so everything a theme
    loads comes from its own folder.
    """
    if not isinstance(relative, str) or not relative or "://" in relative or relative.startswith("data:"):
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    resolved = (root / pure).resolve()
    if not resolved.is_relative_to(root.resolve()):
        return None
    return resolved


def load_theme(theme_id: str, search_dirs: list[Path]) -> Theme:
    root = find_theme_dir(theme_id, search_dirs)
    if root is None:
        raise ThemeError(theme_id, ["not installed"])

    problems: list[str] = []
    warnings: list[str] = []

    try:
        manifest = json.loads((root / "theme.json").read_text())
    except (OSError, ValueError) as e:
        raise ThemeError(theme_id, [f"theme.json: {e}"])
    if not isinstance(manifest, dict):
        raise ThemeError(theme_id, ["theme.json must be an object"])

    _check_manifest(manifest, theme_id, root, problems)

    layouts: dict[str, dict] = {}
    found: set[str] = set()
    layout_dir = root / "layouts"
    if layout_dir.is_dir():
        for path in sorted(layout_dir.glob("*.json")):
            name = path.stem
            found.add(name)
            try:
                tree = json.loads(path.read_text())
            except (OSError, ValueError) as e:
                problems.append(f"layouts/{path.name}: {e}")
                continue
            screen_blocks = _check_node(tree, f"layouts/{path.name}", problems, warnings)
            if name == "frame" and screen_blocks != 1:
                problems.append(f"layouts/frame.json must hold exactly one screen block, found {screen_blocks}")
            if name != "frame" and screen_blocks:
                problems.append(f"layouts/{path.name}: only the frame may hold a screen block")
            layouts[name] = tree
    for name in REQUIRED_LAYOUTS:
        if name not in found:
            problems.append(f"layouts/{name}.json is missing")

    if problems:
        raise ThemeError(theme_id, problems)
    return Theme(id=theme_id, root=root, manifest=manifest, layouts=layouts, warnings=warnings)


def _check_manifest(m: dict, theme_id: str, root: Path, problems: list[str]) -> None:
    if m.get("format") != FORMAT:
        problems.append(f"format must be {FORMAT}, got {m.get('format')!r}")
    if m.get("id") != theme_id:
        problems.append(f"id {m.get('id')!r} doesn't match its folder name {theme_id!r}")
    for key in ("name", "version", "author"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            problems.append(f"{key} is required")

    styles = m.get("styles")
    if not isinstance(styles, list) or not styles:
        problems.append("styles must list at least one CSS file")
    else:
        for s in styles:
            _check_file(root, s, "styles", problems)

    fonts = m.get("fonts", [])
    if not isinstance(fonts, list):
        problems.append("fonts must be a list")
    else:
        for f in fonts:
            if not isinstance(f, dict) or not isinstance(f.get("family"), str):
                problems.append("each font needs a family and a src")
                continue
            _check_file(root, f.get("src"), f"font {f['family']!r}", problems)

    bg = m.get("background")
    if bg is not None:
        if not isinstance(bg, dict) or bg.get("type") not in BACKGROUND_TYPES:
            problems.append("background.type must be image, video or none")
        elif bg["type"] != "none":
            _check_file(root, bg.get("src"), "background", problems)

    theme_icons = m.get("icons", {})
    if not isinstance(theme_icons, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in theme_icons.items()):
        problems.append('icons must map names to icon names, e.g. {"home": "lucide:house"}')

    from services.jb_ui import customize   # (it uses this module)
    customize.check(m, root, problems)

    sizing = m.get("sizing")
    if sizing is not None:
        if not isinstance(sizing, dict) or sizing.get("mode") not in SIZING_MODES:
            problems.append("sizing.mode must be fluid or canvas")
        elif sizing["mode"] == "canvas":
            for dim in ("width", "height"):
                v = sizing.get(dim)
                if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                    problems.append(f"canvas sizing needs a positive whole-number {dim}")


def _check_file(root: Path, relative, what: str, problems: list[str]) -> None:
    path = safe_path(root, relative)
    if path is None:
        problems.append(f"{what}: {relative!r} must be a file inside the theme folder")
    elif not path.is_file():
        problems.append(f"{what}: {relative} not found")


def _check_node(node, where: str, problems: list[str], warnings: list[str]) -> int:
    """Validate one layout node and its children. Returns how many screen
    blocks were found, so the caller can check the frame holds exactly one."""
    if not isinstance(node, dict):
        problems.append(f"{where}: every layout node must be an object")
        return 0

    css_class = node.get("class")
    if css_class is not None and (not isinstance(css_class, str) or not CLASS_RE.match(css_class)):
        problems.append(f"{where}: {css_class!r} isn't a usable CSS class")

    if "block" in node:
        if "children" in node:
            problems.append(f"{where}: a node is a block or a container, not both")
        block = node["block"]
        if not isinstance(block, str):
            problems.append(f"{where}: block must be a name")
            return 0
        if block not in KNOWN_BLOCKS:
            warnings.append(f"{where}: unknown block {block!r} will be skipped")
        for key, value in node.items():
            if key in ("block", "class"):
                continue
            if not SETTING_RE.match(key) or not isinstance(value, (str, int, float, bool)):
                problems.append(f"{where}: block {block!r} setting {key!r} must be a simple value")
        return 1 if block == "screen" else 0

    children = node.get("children")
    if not isinstance(children, list):
        problems.append(f"{where}: a container needs a children list")
        return 0
    for key, value in node.items():
        if key in ("children", "class"):
            continue
        if not SETTING_RE.match(key) or not isinstance(value, (str, int, float, bool)):
            problems.append(f"{where}: container setting {key!r} must be a simple value")
    return sum(_check_node(child, where, problems, warnings) for child in children)


ICON_TYPES = (".svg", ".png", ".webp")
PHOTO_TYPES = (".jpg", ".jpeg", ".png", ".webp")


def named_assets(root: Path, folder: str, types: tuple[str, ...]) -> dict[str, str]:
    """Files in assets/<folder>/, by name (`home.svg` -> "home"), as paths
    relative to the theme folder."""
    found: dict[str, str] = {}
    path = root / "assets" / folder
    if path.is_dir():
        for f in sorted(path.iterdir()):
            if f.suffix.lower() in types and ID_RE.match(f.stem) and f.stem not in found:
                found[f.stem] = f"assets/{folder}/{f.name}"
    return found


def icons(root: Path) -> dict[str, str]:
    """Nav and pager icons: assets/icons/home.svg and so on."""
    return named_assets(root, "icons", ICON_TYPES)


def browse_images(root: Path) -> dict[str, str]:
    """Browse card pictures, named after the sort: assets/browse/artist.jpg."""
    return named_assets(root, "browse", PHOTO_TYPES)


def owner_layouts(theme: Theme, folders: list[tuple[str, Path]]) -> tuple[dict[str, dict], dict[str, str], list[str]]:
    """The theme's layouts with the owner's versions laid over them
    (docs/contracts/configuration.md, Owner layouts).

    `folders` are (origin, folder) pairs, first wins: the admin preview
    passes ("draft", drafts) before ("owner", published); the kiosk only
    ("owner", published). Each folder holds <layout>.json files for this
    theme. An owner layout is checked exactly like a theme's; one that
    fails is skipped, with the reason in the returned problems, and the
    theme's own layout is used. A bad edit never blanks the screen.

    Returns (layouts, origin of each layout, problems).
    """
    layouts = dict(theme.layouts)
    origins = {name: "theme" for name in layouts}
    problems: list[str] = []
    done: set[str] = set()
    for origin, folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            name = path.stem
            if name in done or not ID_RE.match(name):
                continue
            try:
                tree = json.loads(path.read_text())
            except (OSError, ValueError) as e:
                problems.append(f"{origin} layout {name}: {e}")
                continue
            found, _ = check_layout(name, tree)
            if found:
                problems.extend(f"{origin} layout {name}: {p}" for p in found)
                continue
            layouts[name] = tree
            origins[name] = origin
            done.add(name)
    return layouts, origins, problems


def check_layout(name: str, tree) -> tuple[list[str], list[str]]:
    """(problems, warnings) for one layout tree, as a theme's would get."""
    problems: list[str] = []
    warnings: list[str] = []
    if not ID_RE.match(name):
        return [f"{name!r} isn't a usable layout name"], []
    screens = _check_node(tree, f"layout {name}", problems, warnings)
    if name == "frame" and screens != 1:
        problems.append(f"the frame must hold exactly one screen block, found {screens}")
    if name != "frame" and screens:
        problems.append("only the frame may hold a screen block")
    return problems, warnings
