"""jb-source-local: music from USB drives, played by the jukebox itself.

A drive plugged into the jukebox is mounted read-only by deploy/jb-drive
and described in /run/juk3-box-drives. This service:

- scans each drive (scan.py) and publishes its songs, retained, on
  jukebox/jb-source-local/library; jb-library adds them to whatever
  phone's music is showing (always merged, never either/or);
- plays them (player.py) when jb-core sends play_item with an item_path
  "drive:<drive id>/<path on the drive>", and publishes now-playing in the
  phone's shape (jukebox/jb-source-local/now-playing);
- forgets a drive's songs from the screen when it's pulled (jb-library
  keeps their play counts for when it's back).

Music is never copied off the drive; only the scan results are kept, in
/var/lib/juk3-box/drives, so the same drive comes back fast.
"""

import asyncio
import json
import time
from pathlib import Path

from shared import config, logging as jb_logging
from shared.bus import Bus
from shared.files import write_atomic
from services.jb_source_local import scan as scanner
from services.jb_source_local.player import Player

SERVICE_NAME = "jb-source-local"
TOPIC = f"jukebox/{SERVICE_NAME}"
DRIVES = Path("/run/juk3-box-drives")
CACHE = Path("/var/lib/juk3-box/drives")
WATCH_EVERY_S = 2.0
NOW_PLAYING_EVERY_S = 2.0


def mounted_drives() -> dict:
    """{drive id: description} for every drive jb-drive has mounted."""
    drives = {}
    for f in sorted(DRIVES.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if d.get("uuid") and d.get("path") and Path(d["path"]).is_dir():
            drives[d["uuid"]] = d
    return drives


class Source:
    def __init__(self, bus: Bus, log):
        self.bus, self.log = bus, log
        self.drives: dict = {}        # id -> description, as mounted
        self.scans: dict = {}         # id -> scan result
        self.scanning: set = set()
        self.song: dict | None = None  # the song playing (or paused)
        self.player = Player(log, self.changed)

    # -- drives ---------------------------------------------------------

    async def watch(self) -> None:
        while True:
            try:
                await self.check_drives()
            except Exception:
                self.log.exception("checking drives failed")
            await asyncio.sleep(WATCH_EVERY_S)

    async def check_drives(self) -> None:
        now = mounted_drives()
        gone = [d for d in self.drives if d not in now]
        new = [d for d in now if d not in self.drives]
        self.drives = now
        for d in gone:
            self.log.info("drive removed: %s", d)
            self.scans.pop(d, None)
            if self.song and self.song["item_path"].startswith(f"drive:{d}/"):
                await self.player.stop()
        for d in new:
            self.log.info("drive added: %s (%s)", d, now[d].get("label") or "no label")
            self.scanning.add(d)
        if gone or new:
            await self.publish_library()
        for d in new:
            asyncio.create_task(self.scan(d))

    async def scan(self, drive_id: str) -> None:
        drive = self.drives.get(drive_id)
        if not drive:
            return
        cache = CACHE / f"{drive_id}.json"
        try:
            known = json.loads(cache.read_text()).get("files", {})
        except (OSError, ValueError):
            known = {}
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(scanner.scan, Path(drive["path"]), drive_id, known)
        except Exception:
            self.log.exception("scanning %s failed", drive_id)
            self.scanning.discard(drive_id)
            await self.publish_library()
            return
        self.scanning.discard(drive_id)
        if drive_id not in self.drives:
            return                    # pulled while it was being read
        self.scans[drive_id] = result
        self.log.info("drive %s: %d songs, %d pictures, %d videos, %d skipped (%.1fs)",
                      drive_id, len(result["songs"]), len(result["images"]), len(result["videos"]),
                      len(result["skipped"]), time.monotonic() - started)
        try:
            await asyncio.to_thread(write_atomic, cache, json.dumps({"files": result["files"]}))
        except OSError:
            self.log.warning("couldn't save the scan of %s", drive_id)
        await self.publish_library()

    async def publish_library(self) -> None:
        drives, songs = [], []
        for d, info in self.drives.items():
            result = self.scans.get(d) or {}
            drives.append({
                "id": d, "label": info.get("label") or "", "fstype": info.get("fstype"),
                "size": info.get("size"), "scanning": d in self.scanning,
                "songs": len(result.get("songs", [])),
                "images": result.get("images", []), "videos": result.get("videos", []),
                "skipped": result.get("skipped", []),
            })
            songs.extend(result.get("songs", []))
        await self.bus.publish(f"{TOPIC}/library", {"v": 1, "drives": drives, "tracks": songs}, retain=True)

    def file_for(self, item_path: str) -> Path | None:
        if not isinstance(item_path, str) or not item_path.startswith("drive:"):
            return None
        drive_id, _, rel = item_path[len("drive:"):].partition("/")
        drive = self.drives.get(drive_id)
        if not drive or not rel:
            return None
        root = Path(drive["path"]).resolve()
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path

    def song_for(self, item_path: str) -> dict | None:
        drive_id = item_path[len("drive:"):].partition("/")[0]
        for s in (self.scans.get(drive_id) or {}).get("songs", []):
            if s["item_path"] == item_path:
                return s
        return None

    # -- playing --------------------------------------------------------

    def now_playing(self) -> dict:
        p, s = self.player, self.song
        if not s or not p.path:
            return {"status": "stopped", "position_ms": None, "title": None, "artist": None,
                    "album": None, "genre": None, "duration_ms": None, "track_number": None,
                    "number_of_tracks": None}
        return {
            "status": "paused" if p.paused else "playing",
            "position_ms": int(p.position * 1000),
            "title": s.get("title"), "artist": s.get("artist"), "album": s.get("album"),
            "genre": s.get("genre"),
            "duration_ms": int(p.duration * 1000) if p.duration else s.get("duration_ms"),
            "track_number": s.get("album_track"), "number_of_tracks": None,
            "item_path": s.get("item_path"),
        }

    async def changed(self, ended: bool = False, error: bool = False) -> None:
        payload = self.now_playing()
        if ended:
            payload = {**payload, "ended": True}
            if error and self.song:
                await self.bus.publish(f"{TOPIC}/play-error", {"item_path": self.song["item_path"],
                                                               "title": self.song.get("title")})
            self.song = None
        await self.bus.publish(f"{TOPIC}/now-playing", payload)

    async def report(self) -> None:
        """While a song plays, its position every few seconds (as the phone does)."""
        while True:
            await asyncio.sleep(NOW_PLAYING_EVERY_S)
            if self.player.path and not self.player.paused:
                await self.bus.publish(f"{TOPIC}/now-playing", self.now_playing())

    async def command(self, cmd: dict) -> None:
        action = cmd.get("action")
        if action == "play_item":
            item_path = cmd.get("item_path")
            path = self.file_for(item_path)
            song = self.song_for(item_path) if path else None
            if not song:
                self.log.warning("can't play %r: not on a drive that's plugged in", item_path)
                await self.bus.publish(f"{TOPIC}/play-error", {"item_path": item_path, "title": cmd.get("title")})
                return
            self.log.info("playing %s", item_path)
            self.song = song
            await self.player.play(str(path))
        elif action == "pause":
            await self.player.pause()
        elif action == "play":
            await self.player.resume()
        elif action == "stop":
            await self.player.stop()
            self.song = None
        elif action == "rescan":
            for d in list(self.drives):
                self.scanning.add(d)
                asyncio.create_task(self.scan(d))
            await self.publish_library()


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        source = Source(bus, log)
        commands = await bus.subscribe(f"{TOPIC}/cmd")
        await source.publish_library()
        await bus.publish(f"{TOPIC}/now-playing", source.now_playing())
        asyncio.create_task(bus.run_heartbeat())
        asyncio.create_task(source.player.run())
        asyncio.create_task(source.watch())
        asyncio.create_task(source.report())
        async for _topic, payload in commands:
            try:
                await source.command(payload)
            except Exception:
                log.exception("failed to handle command: %r", payload)


if __name__ == "__main__":
    asyncio.run(main())
