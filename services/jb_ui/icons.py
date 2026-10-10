"""Icon library: open-source icon sets bundled with JUKE.BOX, in Iconify's
JSON format (https://iconify.design), looked up by "set:name"
(e.g. "lucide:house").

Bundled on the Pi rather than fetched from the web: the kiosk must work
offline, and its security policy blocks outside downloads anyway. More sets
can be added by dropping another Iconify JSON file in services/jb_ui/icons/
(later, jb-admin can download them).
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from services.jb_ui import markup

ICONS_DIR = Path(__file__).resolve().parent / "icons"

# Icons the engine draws itself rather than from a set: "jb:eq" is the
# animated equalizer (bars that move while a song plays).
BUILT_IN = {"jb:eq"}

# Engine defaults for every button that shows an icon. A theme can replace
# any of them in theme.json ("icons") or with a file in assets/icons/.
DEFAULT_ICONS = {
    "home": "lucide:house",
    "volume": "lucide:volume-2",
    "all-songs": "lucide:music",
    "settings": "lucide:settings",
    "now-playing": "jb:eq",
    "shuffle": "lucide:shuffle",
    "shuffle-on": "lucide:arrow-right",
    "back": "lucide:chevron-left",
    "page-prev": "lucide:chevron-left",
    "page-next": "lucide:chevron-right",
    "see-all": "lucide:chevron-right",
    "card-open": "lucide:chevron-right",
    "play": "lucide:play",
    "favorite": "lucide:heart",
    "close": "lucide:x",
    "connection": "lucide:audio-lines",
    "dropdown": "lucide:chevron-down",
}


@lru_cache(maxsize=None)
def _load_set(prefix: str) -> dict | None:
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", prefix):
        return None
    path = ICONS_DIR / f"{prefix}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if data.get("prefix") == prefix else None


def svg(name: str) -> str | None:
    """The cleaned SVG markup for "set:name", or None if there's no such
    icon. Aliases (Iconify's renamed icons) are followed."""
    if ":" not in name:
        return None
    prefix, icon = name.split(":", 1)
    data = _load_set(prefix)
    if data is None:
        return None
    seen = set()
    while icon not in data.get("icons", {}):
        alias = data.get("aliases", {}).get(icon)
        if not alias or icon in seen:
            return None
        seen.add(icon)
        icon = alias["parent"]
    entry = data["icons"][icon]
    left = entry.get("left", data.get("left", 0))
    top = entry.get("top", data.get("top", 0))
    width = entry.get("width", data.get("width", 16))
    height = entry.get("height", data.get("height", 16))
    raw = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left} {top} {width} {height}" '
           f'width="1em" height="1em">{entry["body"]}</svg>')
    return markup.sanitize(raw, "/")


def resolve(theme_map: dict, theme_files: dict[str, str]) -> dict[str, dict]:
    """Every icon the engine may show, as {name: {"svg": ...} | {"url": ...}
    | {"builtin": ...}}. A theme's own file wins, then the theme's choice of
    library icon, then the engine default."""
    names = set(DEFAULT_ICONS) | set(theme_map) | set(theme_files)
    out: dict[str, dict] = {}
    for name in sorted(names):
        if name in theme_files:
            out[name] = {"url": theme_files[name]}
            continue
        choice = theme_map.get(name, DEFAULT_ICONS.get(name))
        if choice in BUILT_IN:
            out[name] = {"builtin": choice.split(":", 1)[1]}
        elif choice and (markup_svg := svg(choice)):
            out[name] = {"svg": markup_svg}
    return out
