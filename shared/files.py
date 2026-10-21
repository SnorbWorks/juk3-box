"""Writing files so a pulled plug can't leave them broken.

A jukebox gets unplugged mid-write. A file written in place can be left
half-written or empty; written next to the target and renamed over it, it
is either the old file or the new one -- but only if the new data is on
the SD card before the rename, and the rename itself is (fsync of the file,
then of its folder). This is that, for every small state file Juk3box
keeps (the admin's own files go through services/jb_admin/files.py, which
does the same).
"""

import os
import tempfile
from pathlib import Path


def sync_dir(folder: Path) -> None:
    """Make a rename or a new file in `folder` survive a power cut."""
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def sync_file(path: Path) -> None:
    """Flush a file someone else wrote (e.g. ffmpeg's output) to the card."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path: Path, data: bytes | str, mode: int | None = None) -> None:
    """Replace `path` with `data`, all or nothing, even across a power cut."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        elif path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    sync_dir(path.parent)


def replace_synced(tmp: Path, path: Path) -> None:
    """Rename a finished file over `path` once it's safely on the card."""
    sync_file(tmp)
    os.replace(tmp, path)
    sync_dir(Path(path).parent)
