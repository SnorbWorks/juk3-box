"""The owner's media library: pictures and videos for the cabinet's screen
(Image and Logo blocks, Shortcut cards, the background), made right for
the Pi as they're uploaded.

- Pictures larger than the screen are scaled to MAX_SIDE on their longer
  side, turned the way the camera meant (EXIF), and saved compressed. A
  12 MB phone photo becomes a few hundred KB. GIFs are kept as they are
  (they may move).
- Videos are re-encoded in the background for the Pi's hardware decoder
  (jb-ui's background video profile: H.264, at most 1280x720, 30 fps, no
  sound). They show as "optimizing" with progress until done; then the
  copy replaces the original. A restart picks up where it left off.
"""

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path

from services.jb_ui import media as ui_media
from services.jb_ui.main import MEDIA_NAME_RE, MEDIA_TYPES

MAX_SIDE = 1920
REENCODE_OVER = 1_500_000        # bytes: smaller pictures that fit are kept as they are
IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
}
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def sniff(path: Path) -> str | None:
    """The real kind of an uploaded file from its first bytes: an image
    suffix, "video", or None."""
    with open(path, "rb") as f:
        head = f.read(16)
    for magic, suffix in IMAGE_MAGIC.items():
        if head.startswith(magic):
            return suffix
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp" or head[:4] == b"\x1a\x45\xdf\xa3":   # MP4/MOV, Matroska/WebM
        return "video"
    return None


def safe_stem(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(filename or "file").stem).strip("-")[:60]
    return stem or "file"


class MediaLibrary:
    def __init__(self, folder: Path, log: logging.Logger):
        self.folder = folder
        self.incoming = folder / ".incoming"      # videos waiting to be optimized
        self.log = log
        self.jobs: dict[str, dict] = {}            # final name -> {"progress", "error"}
        self._tasks: dict[str, asyncio.Task] = {}

    # Listing -----------------------------------------------------------------

    def items(self) -> list[dict]:
        out = []
        if self.folder.is_dir():
            for p in sorted(self.folder.iterdir(), key=lambda p: -p.stat().st_mtime):
                if p.is_file() and MEDIA_NAME_RE.match(p.name) and p.suffix.lower() in MEDIA_TYPES:
                    kind = "video" if p.suffix.lower() == ".mp4" else "image"
                    out.append({"name": p.name, "url": f"/media/{p.name}", "src": f"/media/{p.name}",
                                "size": p.stat().st_size, "kind": kind})
        for name, job in self.jobs.items():
            out.insert(0, {"name": name, "url": None, "src": None, "kind": "video", "size": job.get("size", 0),
                           "state": "failed" if job.get("error") else "optimizing",
                           "progress": job.get("progress", 0), "error": job.get("error")})
        return out

    def free_name(self, stem: str, suffix: str) -> str:
        name, n = f"{stem}{suffix}", 2
        while (self.folder / name).exists() or name in self.jobs:
            name = f"{stem}-{n}{suffix}"
            n += 1
        return name

    # Adding -------------------------------------------------------------------

    async def add(self, tmp: Path, filename: str) -> dict:
        """Takes an uploaded file (moved or removed). Raises ValueError for
        something that isn't a picture or video."""
        kind = sniff(tmp)
        if kind is None:
            tmp.unlink(missing_ok=True)
            raise ValueError("That isn't a JPEG, PNG, GIF or WebP picture, or an MP4, MOV, WebM or MKV video.")
        self.folder.mkdir(parents=True, exist_ok=True)
        stem = safe_stem(filename)
        if kind == "video":
            name = self.free_name(stem, ".mp4")
            self.incoming.mkdir(parents=True, exist_ok=True)
            src = self.incoming / f"{name}.src"
            shutil.move(str(tmp), src)
            self.start_video(name, src)
            return {"name": name, "kind": "video", "state": "optimizing"}
        name = self.free_name(stem, kind)
        dest = self.folder / name
        try:
            await asyncio.to_thread(optimize_picture, tmp, dest, kind)
        finally:
            tmp.unlink(missing_ok=True)
        return {"name": name, "kind": "image", "url": f"/media/{name}", "src": f"/media/{name}", "size": dest.stat().st_size}

    def delete(self, name: str) -> bool:
        if name in self.jobs:
            task = self._tasks.pop(name, None)
            if task:
                task.cancel()
            self.jobs.pop(name, None)
            (self.incoming / f"{name}.src").unlink(missing_ok=True)
            return True
        path = self.folder / name
        if MEDIA_NAME_RE.match(name) and path.is_file():
            path.unlink()
            return True
        return False

    # Videos -------------------------------------------------------------------

    def resume(self) -> None:
        """Videos a restart interrupted start again."""
        if self.incoming.is_dir():
            for src in sorted(self.incoming.glob("*.src")):
                name = src.name[:-4]
                if MEDIA_NAME_RE.match(name):
                    self.start_video(name, src)

    def start_video(self, name: str, src: Path) -> None:
        self.jobs[name] = {"progress": 0, "size": src.stat().st_size}
        self._tasks[name] = asyncio.get_running_loop().create_task(self._encode(name, src))

    async def _encode(self, name: str, src: Path) -> None:
        part = self.incoming / f"{name}.part"
        try:
            if shutil.which("ffmpeg") is None:
                raise RuntimeError("ffmpeg isn't installed on the jukebox")
            duration = await probe_duration(src)
            cmd = ui_media.encode_command(src, part)
            # Progress on stdout, as key=value lines.
            cmd[cmd.index("-i"):cmd.index("-i")] = ["-progress", "pipe:1", "-nostats"]
            self.log.info("optimizing uploaded video %s", name)
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async for line in proc.stdout:
                key, _, value = line.decode(errors="replace").strip().partition("=")
                if key == "out_time_us" and duration and value.isdigit():
                    self.jobs[name]["progress"] = min(0.99, int(value) / 1e6 / duration)
            err = await proc.stderr.read()
            if await proc.wait() != 0:
                raise RuntimeError(err.decode(errors="replace").strip()[-300:] or "the video couldn't be read")
            part.replace(self.folder / name)
            src.unlink(missing_ok=True)
            self.jobs.pop(name, None)
            self.log.info("video ready: %s (%d KB)", name, (self.folder / name).stat().st_size // 1024)
        except asyncio.CancelledError:
            part.unlink(missing_ok=True)
            raise
        except Exception as e:
            self.log.error("couldn't optimize %s: %s", name, e)
            self.jobs[name] = {**self.jobs.get(name, {}), "error": "This video couldn't be converted. Try an MP4 from a phone or camera."}
            part.unlink(missing_ok=True)
            src.unlink(missing_ok=True)
        finally:
            self._tasks.pop(name, None)

    async def wait(self) -> None:
        """For tests: wait for every running job."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)


async def probe_duration(path: Path) -> float:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return 0.0


def optimize_picture(src: Path, dest: Path, suffix: str) -> None:
    """Writes `src` to `dest`, scaled to fit MAX_SIDE, upright, compressed.
    GIFs, and pictures already small enough, are copied as they are."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(src) as im:
            im.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ValueError("That picture couldn't be read. It may be damaged.")
    if suffix == ".gif":
        shutil.copyfile(src, dest)
        return
    with Image.open(src) as im:
        im.load()
        rotated = im.getexif().get(0x0112, 1) not in (1, None)
        big = max(im.size) > MAX_SIDE
        if not big and not rotated and src.stat().st_size <= REENCODE_OVER:
            shutil.copyfile(src, dest)
            return
        im = ImageOps.exif_transpose(im)
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        tmp = dest.with_name(f".{dest.name}.tmp")
        if suffix == ".png":
            im.save(tmp, "PNG", optimize=True)
        elif suffix == ".webp":
            im.save(tmp, "WEBP", quality=85, method=4)
        else:
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(tmp, "JPEG", quality=85, optimize=True, progressive=True)
        tmp.replace(dest)
