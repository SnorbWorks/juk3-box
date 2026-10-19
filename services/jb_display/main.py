"""jb-display: the screen itself, through sway (the compositor the kiosk
runs in, deploy/kiosk/).

Owns the owner's display settings (display.py): the picture area, so the
screen's contents fit the window in the cabinet's glass, the rotation, and
screen sleep. They're saved in /var/lib/juk3-box/display.json. The picture
area is the kiosk page's to apply (it reads this service's state); the
rotation is applied to sway at once, and again whenever sway starts (the
kiosk restarting starts a new sway, which knows nothing of it).

Bus: state on jukebox/jb-display/state (retained), commands on
jukebox/jb-display/cmd. It watches the knob, the keypad and what's playing
for screen sleep. Runs as the kiosk's user (admin), whose sway it talks to.
"""

import asyncio
import glob
import json
import os
import time
from pathlib import Path

from shared import config, logging as jb_logging
from shared.bus import Bus
from shared.files import write_atomic
from services.jb_display import display

SERVICE_NAME = "jb-display"
DEFAULTS = {"settings_path": "/var/lib/juk3-box/display.json"}

GESTURE = "jukebox/jb-input/gesture"
ENTRY = "jukebox/jb-core/entry"
SOURCES = {
    "jukebox/jb-source-phone/now-playing": "phone",
    "jukebox/jb-source-local/now-playing": "local",
}
PHONE_CONNECTED = "jukebox/jb-source-phone/connected"

WATCH_EVERY_S = 3          # looking for a new sway, and checking sleep
OUTPUTS_EVERY_S = 30       # re-reading the screen's details
SAVE_AFTER_S = 1.5         # a knob turned step by step is saved once it stops
GUIDE_FOR_S = 600          # the alignment guide turns itself off after this
SWAYMSG_TIMEOUT_S = 5


def sway_socket() -> str | None:
    """The running sway's IPC socket. Services aren't started by sway, so
    it isn't in our environment: it's found by name in the user's runtime
    folder. A sway that crashed can leave its socket behind; the newest is
    the one that's running."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    found = glob.glob(f"{runtime}/sway-ipc.{os.getuid()}.*.sock")
    return max(found, key=os.path.getmtime) if found else None


async def swaymsg(sock: str, *args: str) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "swaymsg", "-s", sock, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), SWAYMSG_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError) as e:
        return False, str(e)
    return proc.returncode == 0, (out or err).decode()


def output_info(outputs: list) -> dict | None:
    """The screen the kiosk is on (the first active one), as the admin and
    the cabinet show it. width/height are the panel's mode, unrotated."""
    for o in outputs:
        if not o.get("active"):
            continue
        mode = o.get("current_mode") or {}
        return {"name": o.get("name"), "make": o.get("make"), "model": o.get("model"),
                "width": mode.get("width"), "height": mode.get("height"),
                "refresh": round((mode.get("refresh") or 0) / 1000, 1)}
    return None


class Display:
    def __init__(self, bus: Bus, log, path: Path):
        self.bus = bus
        self.log = log
        self.path = path
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.settings = display.clean(raw)
        self.output: dict | None = None
        self.sock: str | None = None
        self.sleep = display.Sleep(time.monotonic())
        self.guide_until = 0.0
        self._applying = asyncio.Lock()
        self._dirty = False
        self._save_task: asyncio.Task | None = None

    def size(self) -> tuple[int, int] | None:
        if not self.output or not self.output.get("width"):
            return None
        return display.logical_size((self.output["width"], self.output["height"]), self.settings["rotation"])

    def state(self) -> dict:
        return {**self.settings, "asleep": self.sleep.asleep, "guide": time.time() < self.guide_until,
                "output": self.output, "limits": display.limits(self.size()), "sway": self.sock is not None}

    async def publish(self) -> None:
        # Retained: the cabinet's Settings and the admin read it whenever they start.
        await self.bus.publish(f"jukebox/{SERVICE_NAME}/state", self.state(), retain=True)

    # Sway -------------------------------------------------------------------

    async def read_outputs(self) -> None:
        if not self.sock:
            return
        ok, out = await swaymsg(self.sock, "-t", "get_outputs")
        try:
            info = output_info(json.loads(out)) if ok else None
        except ValueError:
            info = None
        if info != self.output:
            self.output = info
            self.log.info("screen: %s", info)

    async def apply(self) -> None:
        """Turn the screen as the settings say. Changes that arrive while
        sway is busy are folded into one more run with the latest values."""
        self._dirty = True
        if self._applying.locked():
            return
        async with self._applying:
            while self._dirty:
                self._dirty = False
                if not self.sock:
                    return
                ok, out = await swaymsg(self.sock, display.sway_layout(self.settings))
                if not ok or '"success": false' in out:
                    self.log.warning("sway refused the layout: %s", out.strip())

    async def power(self, on: bool) -> None:
        if self.sock:
            await swaymsg(self.sock, f"output * power {'on' if on else 'off'}")
        self.log.info("screen %s", "on" if on else "asleep")

    async def watch(self) -> None:
        """A new sway (the kiosk restarted) gets the settings; the screen's
        details are re-read now and then (a different monitor); screen
        sleep is checked."""
        last_outputs = 0.0
        while True:
            try:
                sock = sway_socket()
                if sock != self.sock:
                    self.sock = sock
                    if sock:
                        self.log.info("sway at %s: applying the display settings", sock)
                        await self.read_outputs()
                        await self.apply()
                        # Awake to start with: this service restarting while
                        # the screen slept would otherwise leave it dark.
                        self.sleep.wake(time.monotonic())
                        await self.power(True)
                    await self.publish()
                    last_outputs = time.monotonic()
                elif sock and time.monotonic() - last_outputs > OUTPUTS_EVERY_S:
                    before = self.output
                    await self.read_outputs()
                    last_outputs = time.monotonic()
                    if self.output != before:
                        self.settings["inset"] = display.clamp(self.settings["inset"], self.size())
                        await self.publish()
                if self.sleep.due(time.monotonic(), self.settings["sleep_minutes"]):
                    await self.power(False)
                    await self.publish()
                if self.guide_until and time.time() >= self.guide_until:
                    self.guide_until = 0.0
                    await self.publish()
            except Exception:
                self.log.exception("display watch failed")
            await asyncio.sleep(WATCH_EVERY_S)

    # Settings -----------------------------------------------------------------

    def save_soon(self) -> None:
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
        self._save_task = asyncio.create_task(self._save())

    async def _save(self) -> None:
        await asyncio.sleep(SAVE_AFTER_S)
        try:
            await asyncio.to_thread(write_atomic, self.path, json.dumps(self.settings, indent=2))
        except OSError as e:
            self.log.error("can't save %s: %s", self.path, e)

    async def command(self, cmd: dict) -> None:
        action = cmd.get("action")
        s = self.settings
        if action == "inset":
            # One side: {"action": "inset", "side": "top", "px": 24}
            # or all four: {"action": "inset", "inset": {"top": 24, ...}}
            if isinstance(cmd.get("inset"), dict):
                wanted = {**s["inset"], **{k: v for k, v in cmd["inset"].items() if k in display.SIDES}}
            elif cmd.get("side") in display.SIDES:
                wanted = {**s["inset"], cmd["side"]: cmd.get("px")}
            else:
                self.log.warning("inset: which side? %r", cmd)
                return
            s["inset"] = display.clamp(wanted, self.size())
        elif action == "inset_reset":
            s["inset"] = dict(display.DEFAULTS["inset"])
        elif action == "rotation":
            degrees = cmd.get("degrees")
            if degrees not in display.ROTATIONS:
                self.log.warning("rotation: not one of %s: %r", display.ROTATIONS, degrees)
                return
            s["inset"] = display.remap(s["inset"], s["rotation"], degrees)
            s["rotation"] = degrees
            s["inset"] = display.clamp(s["inset"], self.size())
            await self.apply()
        elif action == "sleep":
            minutes = cmd.get("minutes")
            if minutes not in display.SLEEP_CHOICES:
                self.log.warning("sleep: not one of %s: %r", display.SLEEP_CHOICES, minutes)
                return
            s["sleep_minutes"] = minutes
            self.sleep.wake(time.monotonic())   # the countdown starts from now
        elif action == "wake":
            if self.sleep.wake(time.monotonic()):
                await self.power(True)
        elif action == "guide":
            self.guide_until = time.time() + GUIDE_FOR_S if cmd.get("on") else 0.0
            await self.publish()
            return
        else:
            self.log.warning("unknown command action: %r", action)
            return
        if action != "wake":
            self.save_soon()
            self.log.info("display: %s", {k: s[k] for k in ("inset", "rotation", "sleep_minutes")})
        await self.publish()

    async def heard(self, topic: str, payload: dict) -> None:
        """The controls and the music, for screen sleep."""
        now = time.monotonic()
        if topic in (GESTURE, ENTRY):
            woke = self.sleep.activity(now)
        elif topic in SOURCES:
            woke = self.sleep.source(SOURCES[topic], payload.get("status") == "playing", now)
        elif topic == PHONE_CONNECTED and not payload.get("connected"):
            woke = self.sleep.source("phone", False, now)
        else:
            return
        if woke:
            await self.power(True)
            await self.publish()


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = {**DEFAULTS, **config.load(SERVICE_NAME)}
    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        screen = Display(bus, log, Path(cfg["settings_path"]))
        cmd_topic = f"jukebox/{SERVICE_NAME}/cmd"
        messages = await bus.subscribe(cmd_topic)
        for topic in (GESTURE, ENTRY, *SOURCES, PHONE_CONNECTED):
            await bus.subscribe(topic)
        await screen.publish()
        tasks = [asyncio.create_task(bus.run_heartbeat()), asyncio.create_task(screen.watch())]
        try:
            async for topic, payload in messages:
                try:
                    if topic == cmd_topic:
                        await screen.command(payload)
                    else:
                        await screen.heard(topic, payload)
                except Exception:
                    log.exception("failed to handle %s: %r", topic, payload)
        finally:
            for t in tasks:
                t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
