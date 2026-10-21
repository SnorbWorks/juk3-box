"""Playing a file: one mpv, kept running idle, driven over its JSON socket.

mpv (GPL) runs as its own program; we only talk to it, which keeps
Juk3box's own code its own (docs/extensions-inventory.md). It plays to
PipeWire's default output -- the DAC, the same place the phone's music
goes, so the volume, the analyzer and the lights work unchanged.

on_change() is called whenever what's playing changes: a new song, pause,
resume, stop, or the end of the song (ended=True).
"""

import asyncio
import json
import os
from pathlib import Path

SOCKET = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "jb-source-local-mpv.sock"
MPV = ["mpv", "--idle=yes", "--no-video", "--no-terminal", "--ao=pipewire",
       "--audio-display=no", "--volume=100", "--gapless-audio=no", "--cache=yes",
       "--demuxer-max-bytes=4MiB", f"--input-ipc-server={SOCKET}"]
WATCH = ["pause", "time-pos", "duration", "idle-active"]
START_WAIT_S = 30.0      # mpv's socket should appear by now (a busy Pi can take a while)


class Player:
    def __init__(self, log, on_change):
        self.log = log
        self.on_change = on_change
        self.proc = None
        self.writer = None
        self.reader_task = None
        self.ready = asyncio.Event()
        self.path: str | None = None       # the file loaded, None when stopped
        self.paused = False
        self.position = 0.0
        self.duration = 0.0
        self._req = 0

    async def run(self) -> None:
        """Keep mpv running (restarting it if it dies)."""
        while True:
            try:
                await self._start()
                await self.proc.wait()
                self.log.warning("mpv exited (%s); restarting", self.proc.returncode)
            except Exception:
                self.log.exception("mpv failed")
            # Never leave a half-started mpv behind: starting another on top of it
            # leaked one per retry until the Pi ran out of memory and netwatch
            # rebooted it, over and over (2026-10-19).
            await self._stop_proc()
            self.ready.clear()
            if self.path:
                self.path = None
                await self.on_change(ended=False)
            await asyncio.sleep(2)

    async def _start(self) -> None:
        SOCKET.unlink(missing_ok=True)
        self.proc = await asyncio.create_subprocess_exec(*MPV, stdout=asyncio.subprocess.DEVNULL,
                                                         stderr=asyncio.subprocess.DEVNULL)
        for _ in range(int(START_WAIT_S / 0.05)):
            if SOCKET.exists() or self.proc.returncode is not None:
                break
            await asyncio.sleep(0.05)
        reader, self.writer = await asyncio.open_unix_connection(str(SOCKET))
        for i, prop in enumerate(WATCH, 1):
            await self._send({"command": ["observe_property", i, prop]})
        self.reader_task = asyncio.create_task(self._read(reader))
        self.ready.set()
        self.log.info("mpv ready")

    async def _stop_proc(self) -> None:
        if self.reader_task:
            self.reader_task.cancel()
            self.reader_task = None
        if self.writer:
            self.writer.close()
            self.writer = None
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()

    async def _send(self, msg: dict) -> None:
        self._req += 1
        self.writer.write((json.dumps({**msg, "request_id": self._req}) + "\n").encode())
        await self.writer.drain()

    async def command(self, *args) -> None:
        await asyncio.wait_for(self.ready.wait(), 10)
        await self._send({"command": list(args)})

    async def _read(self, reader) -> None:
        while line := await reader.readline():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            event = msg.get("event")
            if event == "property-change":
                name, value = msg.get("name"), msg.get("data")
                if name == "pause":
                    if bool(value) != self.paused and self.path:
                        self.paused = bool(value)
                        await self.on_change(ended=False)
                    self.paused = bool(value)
                elif name == "time-pos" and isinstance(value, (int, float)):
                    self.position = float(value)
                elif name == "duration" and isinstance(value, (int, float)):
                    self.duration = float(value)
            elif event == "end-file":
                reason = msg.get("reason")
                if reason in ("eof", "error") and self.path:
                    if reason == "error":
                        self.log.warning("mpv couldn't play %s: %s", self.path, msg.get("file_error"))
                    self.path = None
                    self.position = 0.0
                    await self.on_change(ended=True, error=reason == "error")

    async def play(self, path: str) -> None:
        self.path, self.position, self.duration, self.paused = path, 0.0, 0.0, False
        await self.command("loadfile", path, "replace")
        await self.command("set_property", "pause", False)
        await self.on_change(ended=False)

    async def pause(self) -> None:
        if self.path:
            await self.command("set_property", "pause", True)

    async def resume(self) -> None:
        if self.path:
            await self.command("set_property", "pause", False)

    async def stop(self) -> None:
        if self.path:
            self.path = None
            self.position = 0.0
            await self.command("stop")
            await self.on_change(ended=False)
