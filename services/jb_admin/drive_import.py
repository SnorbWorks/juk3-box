"""Pictures and videos from USB drives, into the media library.

jb-source-local lists what's on each drive it has scanned (pictures and
videos with their sizes); this copies each new one into the owner's media
library through the same door an upload uses (media_lib.add: pictures made
right for the screen, videos re-encoded for the Pi). One at a time, in the
background.

- Each file is imported once, ever: the record (drive, path, size) is kept,
  so a picture the owner deleted from the library doesn't come back the
  next time the drive is plugged in. A file changed on the drive (another
  size) counts as new.
- The media library is capped (cap_mb, the owner's "don't fill the card"):
  past it, the rest waits, listed as skipped.
- Music stays on the drive (jb-source-local plays it from there).
"""

import asyncio
import json
import shutil
import time
from pathlib import Path

from shared.files import write_atomic

MOUNTS = Path("/media/juk3-box")


class DriveImport:
    def __init__(self, media, state_path: Path, log, cap_mb: int, file_limit_mb: int):
        self.media = media
        self.state_path = state_path
        self.settings_path = state_path.with_name("drive-settings.json")
        self.log = log
        self.default_cap_mb = cap_mb
        self.last: dict = {}                  # the latest drives payload, for "import again"
        self.file_limit = file_limit_mb * 1024 * 1024
        self.queue: asyncio.Queue = asyncio.Queue()
        self.pending: set[str] = set()
        self.status: dict[str, dict] = {}     # drive id -> {label, imported, waiting, skipped}
        try:
            self.done: dict = json.loads(state_path.read_text())
        except (OSError, ValueError):
            self.done = {}

    # The owner's choices (the Drives page): import automatically, and the cap.
    def settings(self) -> dict:
        try:
            s = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            s = {}
        cap = s.get("cap_mb")
        return {"auto": s.get("auto", True) is not False,
                "cap_mb": cap if isinstance(cap, int) and cap >= 0 else self.default_cap_mb}

    def save_settings(self, auto: bool, cap_mb: int) -> dict:
        write_atomic(self.settings_path, json.dumps({"auto": bool(auto), "cap_mb": max(0, int(cap_mb))}))
        self.heard(self.last)              # turned on: start on what's waiting
        return self.settings()

    @property
    def cap(self) -> int:
        return self.settings()["cap_mb"] * 1024 * 1024

    def reimport(self, drive_id: str, path: str) -> bool:
        """Import a file again (the owner deleted it from the library, or it
        was skipped): forget that it was done, and queue it if it's here."""
        keys = [k for k in self.done if k.rsplit(":", 1)[0] == f"{drive_id}/{path}"]
        for k in keys:
            self.done.pop(k)
        write_atomic(self.state_path, json.dumps(self.done))
        status = self.status.get(drive_id)
        if status:
            status["skipped"] = [s for s in status["skipped"] if s["path"] != path]
        for drive in (self.last.get("drives") or []) if isinstance(self.last, dict) else []:
            if drive.get("id") != drive_id:
                continue
            for item in (drive.get("images") or []) + (drive.get("videos") or []):
                if item.get("path") == path and self.key(drive_id, item) not in self.pending:
                    self.pending.add(self.key(drive_id, item))
                    self.queue.put_nowait((drive_id, item))
                    return True
        return False

    @staticmethod
    def key(drive_id: str, item: dict) -> str:
        return f"{drive_id}/{item['path']}:{item.get('size')}"

    def library_size(self) -> int:
        folder = self.media.folder
        return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0

    def heard(self, payload: dict) -> None:
        """jukebox/jb-source-local/library: queue what's new on each drive
        (unless the owner turned automatic import off)."""
        drives = payload.get("drives") if isinstance(payload, dict) else None
        if not isinstance(drives, list):
            return
        self.last = payload
        auto = self.settings()["auto"]
        for drive in drives:
            drive_id = drive.get("id")
            if not drive_id or drive.get("scanning"):
                continue
            files = [i for i in (drive.get("images") or []) + (drive.get("videos") or [])
                     if isinstance(i, dict) and i.get("path")]
            new = [i for i in files if self.key(drive_id, i) not in self.done]
            skipped = (self.status.get(drive_id) or {}).get("skipped", [])
            self.status[drive_id] = {"label": drive.get("label") or "", "files": len(files),
                                     "imported": len(files) - len(new), "waiting": len(new),
                                     "skipped": [s for s in skipped if any(i["path"] == s["path"] for i in new)]}
            if not auto:
                continue
            for item in new:
                k = self.key(drive_id, item)
                if k not in self.pending:
                    self.pending.add(k)
                    self.queue.put_nowait((drive_id, item))

    async def run(self) -> None:
        while True:
            drive_id, item = await self.queue.get()
            k = self.key(drive_id, item)
            try:
                await self.import_one(drive_id, item)
            except Exception:
                self.log.exception("importing %s failed", k)
            finally:
                self.pending.discard(k)

    def skip(self, drive_id: str, item: dict, reason: str) -> None:
        status = self.status.setdefault(drive_id, {"label": "", "files": 0, "imported": 0, "skipped": []})
        status["skipped"] = [s for s in status["skipped"] if s["path"] != item["path"]]
        status["skipped"].append({"path": item["path"], "reason": reason})
        self.log.info("not importing %s from %s: %s", item["path"], drive_id, reason)

    async def import_one(self, drive_id: str, item: dict) -> None:
        src = MOUNTS / drive_id / item["path"]
        root = (MOUNTS / drive_id).resolve()
        try:
            src = src.resolve()
            size = src.stat().st_size
        except OSError:
            return                                # the drive went away
        if root not in src.parents:
            return
        if size > self.file_limit:
            return self.skip(drive_id, item, "too big")
        if await asyncio.to_thread(self.library_size) + size > self.cap:
            return self.skip(drive_id, item, "media library full")
        self.media.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.media.folder / f".drive-{time.time_ns()}"
        try:
            await asyncio.to_thread(shutil.copyfile, src, tmp)
            added = await self.media.add(tmp, src.name)
        except ValueError:
            added = None                          # not really a picture or video
        except OSError:
            tmp.unlink(missing_ok=True)
            return                                # pulled mid-copy: try again next time
        finally:
            tmp.unlink(missing_ok=True)
        self.done[self.key(drive_id, item)] = {"name": (added or {}).get("name"), "at": int(time.time())}
        await asyncio.to_thread(write_atomic, self.state_path, json.dumps(self.done))
        if drive_id in self.status:
            self.status[drive_id]["imported"] += 1
            self.status[drive_id]["waiting"] = max(0, self.status[drive_id].get("waiting", 1) - 1)
        self.log.info("imported %s from %s as %s", item["path"], drive_id, (added or {}).get("name") or "nothing (not media)")
