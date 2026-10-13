"""The owner settings the admin offers, described as data so the Settings
page draws its form from this (docs/contracts/configuration.md).

Values are checked here and again by jb-ui when it reads them
(services/jb_ui/main.py owner_settings), which stays the authority on
what's usable: the admin refuses what jb-ui would ignore.
"""

from services.jb_ui.main import DEFAULT_NAV, NAV_BUTTONS, owner_settings

NAV_LABELS = {
    "home": "Home",
    "volume": "Volume",
    "shuffle": "Shuffle",
    "all-songs": "All Songs",
    "settings": "Settings",
    "now-playing": "Now Playing",
}

SCHEMA = {
    "nav": {
        "type": "order", "label": "Navigation buttons", "group": "Screen",
        "help": "Which buttons show in the cabinet's navigation, top to bottom. The first is where the knob rests.",
        "options": [{"value": b, "label": NAV_LABELS[b]} for b in NAV_BUTTONS],
        "default": DEFAULT_NAV,
    },
    "songs_per_page": {
        "type": "enum", "label": "Songs per page", "group": "Song board",
        "help": "Each letter gets the same number of slots, so the codes always match the keypad.",
        "options": [{"value": n, "label": f"{n} songs ({n // 10} per letter)"} for n in range(10, 101, 10)],
        "default": 100,
    },
    "idle_home_minutes": {
        "type": "number", "label": "Back to Home after", "group": "Screen", "unit": "minutes",
        "help": "With nothing playing and nobody at the controls. 0 = never.",
        "min": 0, "max": 240, "step": 1, "default": 5,
    },
}


def validate(values: dict) -> tuple[dict, dict]:
    """(clean values, problems by key) for the settings the admin manages."""
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
            ok = (isinstance(value, list) and value and len(set(value)) == len(value)
                  and all(v in NAV_BUTTONS for v in value))
            if not ok:
                problems[key] = "Choose at least one button, each once."
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
