"""Which LED chains this cabinet has: the setup wizard's Lights step
(docs/Setup Wizard — Plan.md, step 9). Lights are optional: a cabinet may
have none, or only some of the three. WS281x strips can't be read back, so
the owner says which are wired in and jb-admin writes it here:

    /var/lib/juk3-box/lights.json   {"buttons": true, "accent": true, "levelbar": false}

No file (a cabinet set up before the wizard) means all three, as before.
"""

import json
from pathlib import Path

PATH = Path("/var/lib/juk3-box/lights.json")
CHAINS = ("buttons", "accent", "levelbar")


def read(path: Path = PATH) -> dict[str, bool]:
    """{chain: wired in}. A chain the file doesn't name counts as there."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {name: data.get(name) is not False for name in CHAINS}

