"""What an installed module needs from the platform besides the bus
(docs/contracts/installed-modules.md): who it is, its owner's settings and
secrets, where to keep its own files.

    from shared.module_sdk import module_id, settings, state_dir
"""

import json
import os
from pathlib import Path


def module_id() -> str:
    return os.environ.get("JB_MODULE_ID", "")


def settings(defaults: dict | None = None) -> dict:
    """The owner's settings from the admin, over `defaults` (the manifest's
    defaults are a good start), with its secrets added. Read it again after
    jukebox/jb-admin/changed names this module."""
    out = dict(defaults or {})
    path = os.environ.get("JB_SETTINGS")
    for source in (Path(path) if path else None, _credential("secrets")):
        if source is None:
            continue
        try:
            data = json.loads(source.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.update(data)
    return out


def state_dir() -> Path:
    """A folder of its own that survives restarts and updates."""
    return Path(os.environ.get("STATE_DIRECTORY", "."))


def _credential(name: str) -> Path | None:
    folder = os.environ.get("CREDENTIALS_DIRECTORY")
    return Path(folder) / name if folder else None
