"""The owner settings the admin offers, described as data so the Settings
page draws its form from this (docs/contracts/configuration.md).

Values are checked here and again by jb-ui when it reads them
(services/jb_ui/main.py owner_settings), which stays the authority on
what's usable: the admin refuses what jb-ui would ignore.
"""

import copy

from services.jb_ui.main import DEFAULT_NAV, NAV_BLOCK_PREFIX, NAV_BUTTONS, NAV_ENGINE_BLOCKS, owner_settings

NAV_LABELS = {
    "home": "Home",
    "volume": "Volume",
    "shuffle": "Shuffle",
    "all-songs": "All Songs",
    "settings": "Settings",
    "now-playing": "Now Playing button",
}

SCHEMA = {
    "nav": {
        "type": "order", "label": "Navigation buttons", "group": "Screen", "modules_label": "Modules",
        "help": "Which buttons show in the cabinet's navigation bar, top to bottom. The first is where the knob rests. The Now Playing button opens the full-screen Now Playing view, which the Now Playing card's heading opens too.",
        "modules_help": "What shows below the buttons, top to bottom: the Now Playing card, Up Next, and modules that can go in the navigation bar. They always come after the buttons.",
        "options": [{"value": b, "label": NAV_LABELS[b], "kind": "button"} for b in NAV_BUTTONS],
        "default": DEFAULT_NAV,
    },
    "idle_home_minutes": {
        "type": "number", "label": "Back to Home after", "group": "Screen", "unit": "minutes",
        "help": "With nothing playing and nobody at the controls. 0 = never.",
        "min": 0, "max": 240, "step": 1, "default": 5,
    },
}


def with_modules(nav_blocks: dict) -> dict:
    """The schema, with what can go below the buttons in the navigation bar
    offered after them: the engine's Now Playing card and Up Next, then the
    module blocks that can go there (`nav_blocks`: block id -> its
    definition)."""
    schema = copy.deepcopy(SCHEMA)
    schema["nav"]["options"] += [{"value": NAV_BLOCK_PREFIX + name, "label": label, "kind": "module"}
                                 for name, label in NAV_ENGINE_BLOCKS.items()]
    schema["nav"]["options"] += [{"value": NAV_BLOCK_PREFIX + name, "label": b["label"], "kind": "module"}
                                 for name, b in sorted(nav_blocks.items())]
    return schema


def validate(values: dict, nav_blocks: dict | None = None) -> tuple[dict, dict]:
    """(clean values, problems by key) for the settings the admin manages.
    `nav_blocks`: the module blocks that may go in the navigation bar."""
    modules = {NAV_BLOCK_PREFIX + name for name in [*NAV_ENGINE_BLOCKS, *(nav_blocks or {})]}
    clean: dict = {}
    problems: dict = {}
    if not isinstance(values, dict):
        return {}, {"_": "Settings must be an object."}
    for key, value in values.items():
        spec = SCHEMA.get(key)
        if spec is None:
            problems[key] = "Not a setting the admin manages."
            continue
        if spec["type"] == "order":
            ok = (isinstance(value, list) and len(set(value)) == len(value)
                  and any(v in NAV_BUTTONS for v in value)
                  and all(v in NAV_BUTTONS or v in modules for v in value))
            if not ok:
                problems[key] = "Choose at least one button, each once, and only modules that can go there."
                continue
        elif spec["type"] == "enum":
            if value not in [o["value"] for o in spec["options"]] or isinstance(value, bool):
                problems[key] = "Not one of the choices."
                continue
        elif spec["type"] == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not spec["min"] <= value <= spec["max"]:
                problems[key] = f"A number from {spec['min']} to {spec['max']}."
                continue
        clean[key] = value
    # Belt and braces: whatever jb-ui would replace with a default is refused.
    used = owner_settings(clean)
    for key, value in clean.items():
        if key in used and used[key] != value:
            problems[key] = "The cabinet can't use this value."
    for key in problems:
        clean.pop(key, None)
    return clean, problems


def current(raw: dict) -> dict:
    """The values in effect, as jb-ui will use them."""
    used = owner_settings(raw)
    return {key: used[key] for key in SCHEMA}


def nav_placement_problems(tree, nav_blocks: dict | None = None) -> list[str]:
    """What a layout puts in the navigation bar (the container holding the
    `nav` block, or anywhere inside it) that Menus > Navigation manages
    instead: the Now Playing card, Up Next, and module blocks that can go
    there (`nav_blocks`). Settings is the only place those go in it."""
    managed = {*NAV_ENGINE_BLOCKS, *(nav_blocks or {})}
    found: list[str] = []

    def walk(node, in_bar: bool) -> None:
        if not isinstance(node, dict):
            return
        children = node.get("children")
        if isinstance(children, list):
            in_bar = in_bar or any(isinstance(c, dict) and c.get("block") == "nav" for c in children)
            for c in children:
                walk(c, in_bar)
        elif in_bar and node.get("block") in managed:
            found.append(node["block"])

    walk(tree, False)
    return [f"{name} goes in the navigation bar from Menus > Navigation, not the page builder"
            for name in dict.fromkeys(found)]
