"""Reading a drive: its songs (with their tags) and its pictures and videos.

Pure apart from reading the files, so it's easy to test. A rescan reuses
what an earlier one read for every file whose size and date haven't
changed (`known`), so plugging the same drive back in costs a directory walk.
"""

import os
from pathlib import Path

from tinytag import TinyTag

AUDIO = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus"}
# Copy-protected iTunes songs (bought before 2009, or Apple Music
# downloads): they play only in Apple's own apps.
PROTECTED = {".m4p"}
IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEOS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
MAX_FILES = 50000          # stop walking a drive this big (a whole backup disk)


def walk(root: Path):
    """Every file under root as (relative path, size, mtime), skipping the
    hidden files and folders Macs and PCs leave on drives (._*, .Trashes,
    .Spotlight-V100, System Volume Information, $RECYCLE.BIN)."""
    count = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "$")) and d != "System Volume Information")
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = Path(folder) / name
            try:
                st = path.stat()
            except OSError:
                continue
            yield path.relative_to(root).as_posix(), st.st_size, int(st.st_mtime)
            count += 1
            if count >= MAX_FILES:
                return


def read_song(path: Path) -> dict | None:
    """A song's tags, or None if the file can't be read as audio."""
    try:
        tag = TinyTag.get(str(path))
    except Exception:
        return None
    if not tag.duration:
        return None
    track = tag.track
    try:
        track = int(str(track).split("/")[0]) if track else None
    except ValueError:
        track = None
    return {
        "title": (tag.title or "").strip() or path.stem,
        "artist": (tag.artist or tag.albumartist or "").strip() or None,
        "album": (tag.album or "").strip() or None,
        "genre": (tag.genre or "").strip() or None,
        "album_track": track,
        "duration_ms": int(tag.duration * 1000),
    }


def scan(root: Path, drive_id: str, known: dict | None = None) -> dict:
    """{songs, images, videos, skipped, files} for the drive mounted at root.

    songs carry item_path "drive:<id>/<relative path>", the handle
    jb-source-local plays them by. files is the cache for the next scan:
    {relative path: [size, mtime, song or None]}."""
    known = known or {}
    songs, images, videos, skipped, files = [], [], [], [], {}
    for rel, size, mtime in walk(root):
        ext = os.path.splitext(rel)[1].lower()
        if ext in PROTECTED:
            skipped.append({"path": rel, "reason": "protected"})
        elif ext in IMAGES:
            images.append({"path": rel, "size": size})
        elif ext in VIDEOS:
            videos.append({"path": rel, "size": size})
        elif ext in AUDIO:
            cached = known.get(rel)
            if cached and cached[0] == size and cached[1] == mtime:
                song = cached[2]
            else:
                song = read_song(root / rel)
            files[rel] = [size, mtime, song]
            if song:
                songs.append({**song, "item_path": f"drive:{drive_id}/{rel}"})
            else:
                skipped.append({"path": rel, "reason": "unreadable"})
    return {"songs": songs, "images": images, "videos": videos, "skipped": skipped, "files": files}
