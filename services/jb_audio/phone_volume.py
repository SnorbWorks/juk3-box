"""The phone's volume and the knob as one volume, the way a car stereo does it.

A connected iPhone uses Bluetooth absolute volume (AVRCP): its slider is a
number 0-127 on the link (BlueZ's MediaTransport1.Volume), and either end
can set it. PipeWire turns that number into the phone stream's master
volume, (n/127)^3, and applies it in software (measured Oct 26, 2026 on
PipeWire 1.4.2). So the slider and the knob would stack: the phone at a
quarter and the knob at 10% plays far quieter than either.

Here the knob stays the volume (the DAC, as before: new sounds, drive songs,
the setup test tone and whole-home all keep working the same), and:

  - the knob's level is sent to the phone (n = level x 127), so its slider
    moves with the knob;
  - the phone's slider moving changes the knob's level (the DAC), so the
    knob, the light bar and the slider stay one number;
  - the phone stream's master volume is put back to 1.0 each time PipeWire
    sets it (setting it here doesn't go back to the phone), so the volume is
    applied once, at the DAC.

Both go through `pw-dump -m` (PipeWire's own change stream, no polling) and
`busctl` (no D-Bus library in jb-audio).
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Awaitable, Callable

AVRCP_MAX = 127
CONNECT_LEVEL = 0.20         # everything starts here when a phone connects
STREAM_PREFIX = "bluez_input."
# A level the phone reports back within this long of us sending it is our own
# echo, not the owner moving the slider (the knob can turn faster than the
# phone answers).
ECHO_S = 1.5
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")

log = logging.getLogger("jb-audio.phone-volume")


def avrcp(level: float) -> int:
    """The knob's level (0-1, as wpctl shows it) as the phone's 0-127."""
    return round(min(max(level, 0.0), 1.0) * AVRCP_MAX)


def level_of(master: float) -> float:
    """The phone stream's master volume, (n/127)^3, back as a level."""
    return round(max(master, 0.0) ** (1 / 3), 3)


def transport_path(tree: str, mac: str) -> str | None:
    """The phone's audio link in `busctl tree org.bluez`: …/dev_88_51_…/fd1,
    or a level down (…/sep1/fd3) after the phone reconnects."""
    dev = "dev_" + mac.upper().replace(":", "_")
    m = re.search(rf"^(/org/bluez/hci\d+/{dev}(?:/sep\d+)?/fd\d+)$", tree, re.M)
    return m.group(1) if m else None


def is_new_connection(mac: str, boot_id: str, last: dict | None) -> bool:
    """A real connect, not jb-audio restarting with the phone still there
    (the connected message is retained, so a restart hears it again)."""
    return not last or last.get("mac") != mac or last.get("boot") != boot_id


def stream_volumes(batch: list, names: dict) -> list[tuple[int, float]]:
    """(node id, master volume) for each phone stream in one pw-dump batch.
    `names` (id -> node name) is kept across batches: an update may carry
    only the part that changed."""
    out = []
    for o in batch:
        nid, info = o.get("id"), o.get("info")
        if info is None:                      # removed
            names.pop(nid, None)
            continue
        name = (info.get("props") or {}).get("node.name")
        if name:
            names[nid] = name
        if not str(names.get(nid, "")).startswith(STREAM_PREFIX):
            continue
        for p in (info.get("params") or {}).get("Props") or []:
            if isinstance(p, dict) and "volume" in p and "channelVolumes" in p:
                out.append((nid, float(p["volume"])))
    return out


def json_batches(buf: str) -> tuple[list, str]:
    """Whole JSON arrays from pw-dump -m's output so far, and the rest."""
    decoder, out, i = json.JSONDecoder(), [], 0
    while True:
        while i < len(buf) and buf[i].isspace():
            i += 1
        try:
            batch, i = decoder.raw_decode(buf, i)
        except ValueError:
            return out, buf[i:]
        out.append(batch)


async def _run(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {err.decode().strip()}")
    return out.decode()


class PhoneVolume:
    """Keeps the connected phone's slider and the knob as one volume.
    `set_level` sets the knob's level (the DAC) and reports it."""

    def __init__(self, set_level: Callable[[float], Awaitable[None]]):
        self.set_level = set_level
        self.mac: str | None = None
        self.level: float | None = None             # the knob's level, as last sent
        self._unsent = False                        # not reached the phone yet (no audio link)
        self._sent: list[tuple[float, int]] = []    # (when, n) we sent the phone

    # the phone coming and going
    def attach(self, mac: str) -> None:
        self.mac = mac
        self._sent.clear()

    def detach(self) -> None:
        self.mac = None
        self._unsent = False

    # knob -> phone
    async def follow(self, level: float) -> None:
        """The knob moved: the phone's slider follows."""
        self.level = level
        if not self.mac:
            return
        n = avrcp(level)
        try:
            path = transport_path(await _run("busctl", "tree", "--list", "org.bluez"), self.mac)
            self._unsent = not path              # no audio link yet: sent when the phone plays
            if not path:
                return
            if int((await _run("busctl", "get-property", "org.bluez", path,
                                "org.bluez.MediaTransport1", "Volume")).split()[1]) == n:
                return
            self._sent = [s for s in self._sent if time.monotonic() - s[0] < ECHO_S] + [(time.monotonic(), n)]
            await _run("busctl", "set-property", "org.bluez", path, "org.bluez.MediaTransport1", "Volume", "q", str(n))
        except (RuntimeError, ValueError, IndexError) as e:
            log.info("couldn't send the volume to the phone: %s", e)

    def _is_echo(self, n: int) -> bool:
        now = time.monotonic()
        return any(now - t < ECHO_S and abs(sent - n) <= 1 for t, sent in self._sent)

    # phone -> knob, and the volume applied once
    async def _stream_changed(self, nid: int, master: float) -> None:
        if abs(master - 1.0) < 1e-3:
            return
        # Back to 1.0 first: the DAC applies the volume (this stays on the jukebox).
        await _run("pw-cli", "set-param", str(nid), "Props", "{ volume: 1.0 }")
        if self._unsent and self.level is not None:
            # Just connected: the stream came up at the phone's old level;
            # the knob's (20% on connect) goes to the phone instead.
            await self.follow(self.level)
            return
        level = level_of(master)
        n = avrcp(level)
        if self._is_echo(n) or (self.level is not None and abs(avrcp(self.level) - n) <= 1):
            return                               # ours coming back, or no change
        log.info("the phone's slider: %.0f%%", level * 100)
        self.level = level
        await self.set_level(level)

    async def run(self) -> None:
        """Watches PipeWire for the phone stream's volume, for as long as
        jb-audio runs (pw-dump -m restarted if PipeWire is)."""
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pw-dump", "-m", "-N", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                names: dict = {}
                buf = ""
                while chunk := await proc.stdout.read(65536):
                    batches, buf = json_batches(buf + chunk.decode(errors="replace"))
                    for batch in batches:
                        for nid, master in stream_volumes(batch, names):
                            try:
                                await self._stream_changed(nid, master)
                            except RuntimeError as e:
                                log.info("phone stream %s: %s", nid, e)
                await proc.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watching the phone's volume failed")
            await asyncio.sleep(2)


def boot_id() -> str:
    try:
        return BOOT_ID.read_text().strip()
    except OSError:
        return ""
