"""Themes from the admin's side: list them, install one from a zip, delete
one the owner added.

A theme is data only (docs/theme-format.md). An uploaded zip is checked
before anything is unpacked (no paths outside the theme, no links, size
limits), unpacked to a holding folder, stripped to the file types a theme
may contain, validated exactly as jb-ui will load it, and only then moved
into place. A failed install leaves nothing behind.
"""

import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from services.jb_ui import themes

MAX_FILES = 2000
MAX_TOTAL = 800 * 1024 * 1024        # unpacked; background videos are big
MAX_ONE = 500 * 1024 * 1024

# What a theme may contain. Anything else (scripts, programs) is dropped.
KEEP = {
    ".json", ".css", ".html", ".txt", ".md",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif",
    ".woff", ".woff2", ".ttf", ".otf",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv",
}
KEEP_NAMES = {"LICENSE", "LICENCE", "README", "COPYING"}


class InstallError(Exception):
    pass


def describe(theme_id: str, dirs: list[Path], active: str) -> dict:
    """One theme for the theme list, usable or not."""
    root = themes.find_theme_dir(theme_id, dirs)
    info = {"id": theme_id, "active": theme_id == active, "shipped": bool(root) and root.parent == dirs[0],
            "problems": [], "warnings": []}
    try:
        t = themes.load_theme(theme_id, dirs)
        m = t.manifest
        info.update({k: m.get(k) for k in ("name", "version", "author", "description", "license")})
        info["warnings"] = t.warnings
        info["layouts"] = sorted(t.layouts)
        info["_manifest"] = m
    except themes.ThemeError as e:
        info["name"] = theme_id
        info["problems"] = e.problems
        info["layouts"] = []
    if root:
        # The theme's own picture of itself, else its background picture.
        candidates = ["preview.png", "preview.jpg", "preview.webp"]
        bg = (info.get("_manifest") or {}).get("background") or {}
        if bg.get("type") == "image" and isinstance(bg.get("src"), str):
            candidates.append(bg["src"])
        candidates += ["assets/background.jpg", "assets/background.png", "assets/background.webp"]
        for name in candidates:
            path = themes.safe_path(root, name)
            if path and path.is_file():
                info["preview"] = f"/themes/{theme_id}/{name}"
                break
    info.pop("_manifest", None)
    return info


def list_all(dirs: list[Path], active: str) -> list[dict]:
    return [describe(t, dirs, active) for t in themes.list_themes(dirs)]


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath | None:
    """The member's path, or None if it must be skipped (folders). Raises
    for anything dangerous."""
    name = info.filename.replace("\\", "/")
    if info.is_dir():
        return None
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise InstallError(f"{name}: links aren't allowed in a theme")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise InstallError(f"{name}: points outside the theme")
    if path.parts[0] == "__MACOSX" or path.name.startswith("._") or path.name == ".DS_Store":
        return None
    return path


def install_zip(zip_path: Path, added_dir: Path, dirs: list[Path], replace: bool = False) -> dict:
    """Installs the theme in `zip_path` into `added_dir`. `dirs` are all
    theme folders, shipped first. Returns the installed theme's description
    plus what was dropped."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise InstallError("That isn't a zip file.")
    with zf:
        members = []
        total = 0
        for info in zf.infolist():
            path = _safe_member(info)
            if path is None:
                continue
            if info.file_size > MAX_ONE:
                raise InstallError(f"{path}: too big ({info.file_size // 1048576} MB)")
            total += info.file_size
            members.append((info, path))
        if len(members) > MAX_FILES:
            raise InstallError(f"Too many files ({len(members)}); a theme may have {MAX_FILES}.")
        if total > MAX_TOTAL:
            raise InstallError(f"Too big unpacked ({total // 1048576} MB); the limit is {MAX_TOTAL // 1048576} MB.")

        # theme.json at the top, or inside one top folder (how most zips are made).
        tops = {p.parts[0] for _, p in members}
        if any(p.parts == ("theme.json",) for _, p in members):
            strip = 0
        elif len(tops) == 1 and any(p.parts[1:] == ("theme.json",) for _, p in members):
            strip = 1
        else:
            raise InstallError("No theme.json at the top of the zip (or in its one top folder).")

        manifest_member = next(i for i, p in members if p.parts[strip:] == ("theme.json",))
        try:
            manifest = json.loads(zf.read(manifest_member))
            theme_id = manifest["id"]
        except (ValueError, KeyError, TypeError):
            raise InstallError("theme.json isn't readable, or has no id.")
        if not isinstance(theme_id, str) or not themes.ID_RE.match(theme_id):
            raise InstallError("The theme's id must be lowercase letters, numbers and hyphens.")
        shipped = themes.find_theme_dir(theme_id, dirs[:1])
        if shipped:
            raise InstallError(f"\"{theme_id}\" is a theme that comes with Juk3box. Give yours another id.")
        existing = added_dir / theme_id
        if existing.exists() and not replace:
            raise InstallError(f"A theme called \"{theme_id}\" is already installed.")

        added_dir.mkdir(parents=True, exist_ok=True)
        holding = Path(tempfile.mkdtemp(prefix=".incoming-", dir=added_dir))
        try:
            dest = holding / theme_id
            dest.mkdir()
            dropped = []
            for info, path in members:
                rel = PurePosixPath(*path.parts[strip:])
                if not rel.parts:
                    continue
                if rel.suffix.lower() not in KEEP and rel.name not in KEEP_NAMES:
                    dropped.append(str(rel))
                    continue
                target = dest.joinpath(*rel.parts)
                if not target.resolve().is_relative_to(dest.resolve()):
                    raise InstallError(f"{rel}: points outside the theme")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)

            try:
                themes.load_theme(theme_id, [holding])
            except themes.ThemeError as e:
                raise InstallError("The theme has problems: " + "; ".join(e.problems))

            if existing.exists():
                old = holding / ".replaced"
                existing.rename(old)
            dest.rename(existing)
        finally:
            shutil.rmtree(holding, ignore_errors=True)

    info = describe(theme_id, dirs, active="")
    info["dropped"] = dropped
    return info


def delete(theme_id: str, added_dir: Path, dirs: list[Path], active: str) -> None:
    if not themes.ID_RE.match(theme_id):
        raise InstallError("No such theme.")
    if themes.find_theme_dir(theme_id, dirs[:1]):
        raise InstallError("Themes that come with Juk3box can't be deleted.")
    if theme_id == active:
        raise InstallError("That's the theme the cabinet is using. Switch to another theme first.")
    folder = added_dir / theme_id
    if not (folder / "theme.json").is_file():
        raise InstallError("No such theme.")
    shutil.rmtree(folder)
