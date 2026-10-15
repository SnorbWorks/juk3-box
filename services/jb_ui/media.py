"""Background video optimization: a theme can ship any video (any size,
frame rate or format ffmpeg reads); the kiosk plays a copy re-encoded for
the hardware.

The Pi 4 decodes H.264 in hardware up to 1280x720 here (1080p failed to
play; a 2560x1440 original can't use the decoder at all), and 30 fps suits
the 60 Hz screen (24 fps scrolled worse). Measured 2026-10-10 with
Chromium's zero-copy hardware decoding on: 720p30 scrolled at 47-54 fps
using ~57% of one core.

The copy is made once, in the background, at the lowest priority and with
settings that fit in memory next to the running kiosk (x264 "slow" at 1080p
was killed for lack of memory). Until it's ready the original is served.
Copies live outside the theme (themes stay untouched, and updates don't
delete them) and are remade when the original changes.
"""

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path

from shared.files import replace_synced

VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}

# Bump when the encoding below changes, so every copy is remade.
PROFILE_VERSION = 1
MAX_WIDTH, MAX_HEIGHT, FPS = 1280, 720, 30


def encode_command(src: Path, dest: Path) -> list[str]:
    scale = (f"scale=w='min({MAX_WIDTH},iw)':h='min({MAX_HEIGHT},ih)'"
             ":force_original_aspect_ratio=decrease:flags=bicubic,"
             "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
             f"fps={FPS}")
    return [
        "nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-threads", "2", "-i", str(src),
        "-vf", scale,
        "-c:v", "libx264", "-preset", "veryfast", "-x264-params", "rc-lookahead=10:threads=2",
        "-crf", "23", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-an", "-movflags", "+faststart", "-f", "mp4", str(dest),
    ]


class VideoOptimizer:
    def __init__(self, cache_dir: Path, log: logging.Logger):
        self.cache_dir = cache_dir
        self.log = log
        self._jobs: dict[Path, asyncio.Task] = {}
        self._failed: set[Path] = set()

    @staticmethod
    def is_video(path: Path) -> bool:
        return path.suffix.lower() in VIDEO_SUFFIXES

    def copy_path(self, src: Path) -> Path:
        """Where the optimized copy of `src` (as it is now) lives."""
        st = src.stat()
        source_id = hashlib.sha256(str(src.resolve()).encode()).hexdigest()[:12]
        state = hashlib.sha256(f"{PROFILE_VERSION}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:12]
        return self.cache_dir / f"{source_id}-{state}.mp4"

    def served_path(self, src: Path) -> Path:
        """The file to send for `src`: its optimized copy when ready, else
        the original (and the copy gets started)."""
        dest = self.copy_path(src)
        if dest.is_file():
            return dest
        self.start(src, dest)
        return src

    def start(self, src: Path, dest: Path) -> None:
        if dest in self._jobs or dest in self._failed:
            return
        if shutil.which("ffmpeg") is None:
            self.log.warning("ffmpeg isn't installed; background videos play as shipped")
            self._failed.add(dest)
            return
        self._jobs[dest] = asyncio.get_running_loop().create_task(self._encode(src, dest))

    async def _encode(self, src: Path, dest: Path) -> None:
        tmp = dest.with_suffix(".part")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.log.info("optimizing background video %s (%s, %d fps)", src.name, f"{MAX_WIDTH}x{MAX_HEIGHT} max", FPS)
            proc = await asyncio.create_subprocess_exec(
                *encode_command(src, tmp), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(err.decode(errors="replace").strip()[-400:] or f"ffmpeg exited {proc.returncode}")
            # On the card before it's taken as done: a power cut right after
            # must not leave a broken video the kiosk keeps playing.
            replace_synced(tmp, dest)
            # Older copies of the same video are no longer needed.
            prefix = dest.name.split("-")[0] + "-"
            for old in self.cache_dir.glob(prefix + "*.mp4"):
                if old != dest:
                    old.unlink(missing_ok=True)
            self.log.info("background video ready: %s (%d KB)", dest.name, dest.stat().st_size // 1024)
        except Exception as e:
            self.log.error("couldn't optimize %s: %s", src.name, e)
            self._failed.add(dest)
            tmp.unlink(missing_ok=True)
        finally:
            self._jobs.pop(dest, None)

    async def wait(self) -> None:
        """For tests: wait for every running job."""
        while self._jobs:
            await asyncio.gather(*list(self._jobs.values()), return_exceptions=True)
