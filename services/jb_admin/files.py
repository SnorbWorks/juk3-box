"""The owner's data on disk (docs/contracts/configuration.md): settings,
owner layouts and drafts, the media library. jb-admin is their only writer.

Every write is atomic: a temporary file in the same folder, then a rename
over the old one, so jb-ui never reads half a file.
"""

import json
import os
import re
import tempfile
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def write_bytes(path: Path, data: bytes, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600 if private else 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path: Path, data, private: bool = False) -> None:
    write_bytes(path, (json.dumps(data, indent=2) + "\n").encode(), private=private)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Layouts:
    """Owner layouts per theme: published (the cabinet shows these) and
    drafts (only the preview shows these)."""

    def __init__(self, published: Path, drafts: Path):
        self.published = published
        self.drafts = drafts

    def _path(self, root: Path, theme: str, name: str) -> Path:
        if not ID_RE.match(theme) or not ID_RE.match(name):
            raise ValueError("bad theme or layout name")
        return root / theme / f"{name}.json"

    def published_path(self, theme: str, name: str) -> Path:
        return self._path(self.published, theme, name)

    def draft_path(self, theme: str, name: str) -> Path:
        return self._path(self.drafts, theme, name)

    def get(self, theme: str, name: str) -> tuple[dict | None, dict | None]:
        """(published, draft); None where there isn't one."""
        return read_json(self.published_path(theme, name)), read_json(self.draft_path(theme, name))

    def names(self, theme: str) -> dict[str, dict]:
        """Which layouts the owner has touched: {name: {published, draft}}."""
        found: dict[str, dict] = {}
        for kind, root in (("published", self.published), ("draft", self.drafts)):
            folder = root / theme if ID_RE.match(theme) else None
            if folder and folder.is_dir():
                for p in folder.glob("*.json"):
                    if ID_RE.match(p.stem):
                        found.setdefault(p.stem, {"published": False, "draft": False})[kind] = True
        return found

    def save_draft(self, theme: str, name: str, tree: dict) -> None:
        write_json(self.draft_path(theme, name), tree)

    def discard_draft(self, theme: str, name: str) -> bool:
        try:
            self.draft_path(theme, name).unlink()
            return True
        except FileNotFoundError:
            return False

    def publish(self, theme: str, name: str) -> bool:
        """The draft becomes the published layout. False if there's no draft."""
        draft = self.draft_path(theme, name)
        if not draft.is_file():
            return False
        target = self.published_path(theme, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(draft, target)
        return True

    def reset(self, theme: str, name: str) -> None:
        """Back to the theme's own layout: the owner's version and draft go."""
        for p in (self.published_path(theme, name), self.draft_path(theme, name)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def forget_theme(self, theme: str) -> None:
        """Everything the owner saved for a theme that's being deleted."""
        for root in (self.published, self.drafts):
            folder = root / theme
            if ID_RE.match(theme) and folder.is_dir():
                for p in folder.glob("*.json"):
                    p.unlink()
                try:
                    folder.rmdir()
                except OSError:
                    pass
