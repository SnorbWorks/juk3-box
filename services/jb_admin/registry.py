"""The module registry (docs/contracts/modules.md): which modules are
installed (their module.json) and which are up (their heartbeat on the bus).

Not a service of its own: a view built from those two things, so the core
keeps no list of modules and a new module appears by being installed.
"""

import json
import time
from pathlib import Path

ROLES = ("source", "analyzer", "input", "output", "service")
UP_WITHIN_S = 10.0


def read_manifests(folders: list[Path]) -> dict[str, dict]:
    """Every module.json under `folders` (one level down), by id. A broken
    manifest is listed with its problem rather than hidden."""
    found: dict[str, dict] = {}
    for i, folder in enumerate(folders):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*/module.json")):
            try:
                m = json.loads(path.read_text())
                if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                    raise ValueError("needs an id")
            except (OSError, ValueError) as e:
                found[path.parent.name] = {"id": path.parent.name, "name": path.parent.name,
                                           "problem": f"module.json: {e}", "roles": []}
                continue
            m = dict(m)
            m["roles"] = [r for r in m.get("roles", []) if r in ROLES]
            # The first folder holds the built-in modules; the rest are
            # installed (docs/contracts/extensions.md).
            m["installed"] = i > 0
            found.setdefault(m["id"], m)
    return found


class Registry:
    def __init__(self, folders: list[Path], clock=time.time):
        self.folders = folders
        self.clock = clock
        self.heartbeats: dict[str, float] = {}   # module id -> when last heard
        self.status: dict[str, bool] = {}       # module id -> retained status online

    def heard(self, topic: str, payload: dict) -> None:
        """Feed every jukebox/+/heartbeat and .../status message here."""
        parts = topic.split("/")
        if len(parts) == 3 and parts[2] == "heartbeat":
            self.heartbeats[parts[1]] = self.clock()
        elif parts[-1] == "status" and isinstance(payload, dict) and "online" in payload:
            module = payload.get("module") or parts[1]
            self.status[module] = bool(payload["online"])

    def view(self, systemd: dict[str, str] | None = None) -> list[dict]:
        """Every module, declared or merely heard, with whether it's up.
        `systemd` (unit -> state) is the fallback for modules that don't
        heartbeat yet."""
        now = self.clock()
        manifests = read_manifests(self.folders)
        ids = list(manifests) + [m for m in list(self.heartbeats) + list(self.status) if m not in manifests]
        out = []
        for mid in dict.fromkeys(ids):
            m = manifests.get(mid, {"id": mid, "name": mid, "roles": [], "undeclared": True})
            last = self.heartbeats.get(mid)
            if mid in self.status:
                up, how = self.status[mid], "status"
            elif last is not None:
                up, how = now - last < UP_WITHIN_S, "heartbeat"
            elif systemd and mid in systemd:
                up, how = systemd[mid] == "active", "systemd"
            else:
                up, how = None, "unknown"
            out.append({**m, "up": up, "up_from": how,
                        "last_heard": None if last is None else round(now - last, 1)})
        return sorted(out, key=lambda m: (m.get("undeclared", False), m["id"]))
