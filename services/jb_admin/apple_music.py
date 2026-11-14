"""Sources > Apple Music's shortcuts: where Apple Music shows on the cabinet
(docs/contracts/apple-music.md, "The admin").

The navigation button lives in Menus > Navigation (the owner settings'
`nav`); the Browse card and the Home row live in the Home layout, which the
page builder edits. These are pure functions over those: whether each is
there, and a copy with it added. main.py writes the results the way the
page builder does (a draft, published).
"""

import copy

NAV_BUTTON = "apple-music"
SORT = "apple-music"
SECTION = "apple-music"
# The Browse section's cards when its layout doesn't say (the engine's
# sections.js DEFAULT_SORTS).
DEFAULT_SORTS = "artist,album,top-100,most-played"
REFRESH_CHOICES = (6, 24, 168)
GENRE_LIMIT = 60


def _walk(node, parent=None):
    """(node, its parent) for every node, depth first, in order."""
    if not isinstance(node, dict):
        return
    yield node, parent
    for child in node.get("children") or []:
        yield from _walk(child, node)


def _is_section(node, kind: str) -> bool:
    return node.get("block") == "section" and node.get("type") == kind


def _browse(tree):
    """The first Browse section and the container holding it, or (None, None)."""
    for node, parent in _walk(tree):
        if _is_section(node, "browse"):
            return node, parent
    return None, None


def sorts_of(section: dict) -> list[str]:
    raw = section.get("sorts")
    raw = raw if isinstance(raw, str) and raw.strip() else DEFAULT_SORTS
    return [s.strip() for s in raw.split(",") if s.strip()]


def has_browse_card(tree) -> bool:
    section, _ = _browse(tree)
    return bool(section) and SORT in sorts_of(section)


def can_add_browse_card(tree) -> bool:
    return _browse(tree)[0] is not None


def add_browse_card(tree):
    """A copy of the Home layout with apple-music at the end of the first
    Browse section's cards; None if it has no Browse section or already
    has the card."""
    tree = copy.deepcopy(tree)
    section, _ = _browse(tree)
    if section is None or SORT in sorts_of(section):
        return None
    section["sorts"] = ",".join([*sorts_of(section), SORT])
    return tree


def has_home_row(tree) -> bool:
    return any(_is_section(node, SECTION) for node, _ in _walk(tree))


def _rows(tree):
    """The container Home's rows are in: home-sections, else the Browse
    section's, else the layout itself."""
    for node, _ in _walk(tree):
        if "home-sections" in str(node.get("class") or "").split() and isinstance(node.get("children"), list):
            return node
    _, parent = _browse(tree)
    if parent is not None:
        return parent
    return tree if isinstance(tree, dict) and isinstance(tree.get("children"), list) else None


def add_home_row(tree):
    """A copy of the Home layout with an Apple Music row right after the
    Browse section (at the end of the rows if there's none); None if it's
    there already or there's nowhere to put it."""
    tree = copy.deepcopy(tree)
    rows = _rows(tree)
    if rows is None or has_home_row(tree):
        return None
    row = {"block": "section", "type": SECTION}
    children = rows["children"]
    at = next((i + 1 for i, c in enumerate(children) if isinstance(c, dict) and _is_section(c, "browse")), len(children))
    children.insert(at, row)
    return tree


def add_nav_button(nav: list, buttons) -> list | None:
    """The navigation list with the Apple Music button after the other
    buttons (modules stay below them); None if it's there already."""
    if NAV_BUTTON in nav:
        return None
    last = max((i for i, v in enumerate(nav) if v in buttons), default=-1)
    return [*nav[:last + 1], NAV_BUTTON, *nav[last + 1:]]


def settings_changes(body: dict) -> tuple[dict, str | None]:
    """The apple_music settings in an admin request: (changes, problem)."""
    out: dict = {}
    for k in ("on", "clean"):
        if k in body:
            out[k] = bool(body[k])
    if "refresh_hours" in body:
        if body["refresh_hours"] not in REFRESH_CHOICES or isinstance(body["refresh_hours"], bool):
            return {}, "Refresh every 6 hours, 24 hours or a week."
        out["refresh_hours"] = body["refresh_hours"]
    if "genres" in body:
        g = body["genres"]
        if g is not None and not (isinstance(g, list) and len(g) <= GENRE_LIMIT
                                  and all(isinstance(i, str) and 0 < len(i) <= 100 for i in g)):
            return {}, "Which genres?"
        out["genres"] = None if g is None else list(dict.fromkeys(g))
    return out, None
