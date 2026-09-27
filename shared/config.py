"""Config loading shared by every jb-* service.

Each service reads /etc/juk3-box/<service>.json if present, falling back to
defaults. Keeping this file-based (not env vars) so config can be edited on
the Pi without touching the systemd unit.
"""

import json
from pathlib import Path

DEFAULTS = {
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
}


def load(service_name: str) -> dict:
    config = dict(DEFAULTS)
    path = Path(f"/etc/juk3-box/{service_name}.json")
    if path.exists():
        config.update(json.loads(path.read_text()))
    return config
