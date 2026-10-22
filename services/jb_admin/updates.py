"""The admin's Updates tab: everything on the jukebox that updates, in one
place, with one switch for automatic updates.

  - Juk3box itself, from the project's releases (deploy/jb-update).
  - Raspberry Pi OS: security fixes (automatic when the switch is on) and
    everything else (only when the owner asks). jb-update does the work
    as root; its state's "os" is read here.
  - The keypad board's program (installed from the controls step).
  - What comes inside each Juk3box release (the Python packages, MQTT.js,
    the icons, CircuitPython): listed, never updated one by one: a
    release is tested as a whole.
  - The main programs from Raspberry Pi OS, with their versions.
  - The iPhone app: the App Store updates it.

Restarts: some OS updates only take effect after one. With automatic
updates on, the admin restarts the jukebox overnight (RESTART_HOURS) when
nothing is playing; otherwise the tab offers a Restart button.
"""

import asyncio
import json
import re
import subprocess
import time
from pathlib import Path

from services.jb_admin.files import read_json

REPO = Path(__file__).resolve().parents[2]
RESTART_HOURS = range(3, 5)          # local time
RESTART_CHECK_S = 300
NOW_PLAYING = ("now_playing", "now_playing_local", "now_playing_helper")
# The Raspberry Pi OS programs the jukebox leans on (THIRD-PARTY.md).
SYSTEM = (("chromium", "Chromium", "Shows the song board"), ("pipewire", "PipeWire", "Sound"),
          ("wireplumber", "WirePlumber", "Sound"), ("bluez", "BlueZ", "Bluetooth"),
          ("shairport-sync", "Shairport Sync", "AirPlay"), ("mosquitto", "Mosquitto", "The message bus"),
          ("sway", "Sway", "The screen"), ("ffmpeg", "FFmpeg", "Video and audio"), ("mpv", "mpv", "Video"))


def boot_time() -> float:
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except OSError:
        pass
    return 0.0


def included(repo: Path = REPO) -> list[dict]:
    """What ships inside a Juk3box release, with its version (from the files)."""
    out = []
    try:
        wanted = {re.split(r"[<>=\[ ]", l.strip())[0].lower()
                  for l in (repo / "deploy/requirements.txt").read_text().splitlines()
                  if l.strip() and not l.startswith("#")}
        for line in (repo / "deploy/install/constraints.txt").read_text().splitlines():
            if "==" in line and not line.startswith("#"):
                name, version = line.strip().split("==", 1)
                if name.lower() in wanted:
                    out.append({"name": name, "version": version, "kind": "Python package"})
    except OSError:
        pass
    for js in sorted((repo / "services/jb_ui/engine").glob("mqtt-*.min.js")):
        out.append({"name": "MQTT.js", "version": js.name[5:-7], "kind": "The screen's link to the jukebox"})
    lucide = read_json(repo / "services/jb_ui/icons/lucide.json", {})
    if isinstance(lucide, dict) and lucide.get("lastModified"):
        out.append({"name": "Lucide icons", "version": time.strftime("%b %-d, %Y", time.gmtime(lucide["lastModified"])),
                    "kind": "Icons"})
    for uf2 in (repo / "firmware/pico-keypad/vendor").glob("adafruit-circuitpython-*.uf2"):
        m = re.search(r"-(\d+\.\d+\.\d+)\.uf2$", uf2.name)
        if m:
            out.append({"name": "CircuitPython", "version": m.group(1), "kind": "For the keypad board"})
    return out


def system_versions() -> list[dict]:
    names = [p for p, _, _ in SYSTEM]
    try:
        r = subprocess.run(["dpkg-query", "-W", "-f", "${Package} ${Version}\n", *names],
                           capture_output=True, text=True, timeout=10)
        have = dict(line.split(" ", 1) for line in r.stdout.splitlines() if " " in line)
    except (OSError, subprocess.SubprocessError):
        have = {}
    return [{"name": label, "does": does, "version": have[p]} for p, label, does in SYSTEM if p in have]


class Updates:
    def __init__(self, admin):
        self.admin = admin
        self._system = None          # dpkg's answer, once per start (it changes only with an update)

    def folder(self) -> Path:
        return Path(self.admin.cfg["update_dir"])

    def os_view(self) -> dict:
        s = read_json(self.folder() / "state.json", {})
        o = s.get("os") if isinstance(s, dict) else None
        o = o if isinstance(o, dict) else {}
        restart = bool(o.get("restart_needed_at")) and o["restart_needed_at"] > boot_time()
        return {"checked_at": o.get("checked_at"), "security": o.get("security") or [], "other": o.get("other") or [],
                "phase": o.get("phase"), "what": o.get("what"), "error": o.get("error"),
                "updated_at": o.get("updated_at"), "updated": o.get("updated") or [], "restart_needed": restart}

    def keypad_view(self) -> dict:
        from services.jb_admin.setup_wizard import keypad_latest, keypad_running
        running = keypad_running((self.admin.live.get("input") or {}).get("keypad"))
        latest = keypad_latest()
        return {"running": running, "latest": latest,
                "available": bool(running and latest and running != latest)}

    def announcement(self, j: dict, o: dict, k: dict) -> dict | None:
        """The dashboard's one line about updates, if there's something to say."""
        if o["restart_needed"]:
            return {"kind": "restart", "text": "Updates are installed and take effect when the jukebox restarts."}
        ready = []
        if j.get("available"):
            ready.append(f"Juk3box {j['latest']}")
        if o["security"]:
            ready.append(f"{len(o['security'])} security fix{'es' if len(o['security']) != 1 else ''}")
        if o["other"]:
            ready.append(f"{len(o['other'])} Raspberry Pi OS update{'s' if len(o['other']) != 1 else ''}")
        if k["available"]:
            ready.append(f"keypad program {k['latest']}")
        if ready:
            return {"kind": "available", "text": "Ready to install: " + ", ".join(ready) + "."}
        day = 3 * 86400
        if j.get("updated_at") and time.time() - j["updated_at"] < day:
            return {"kind": "updated", "text": f"Juk3box updated itself to {j['current']}."}
        if o["updated_at"] and time.time() - o["updated_at"] < day and o["updated"]:
            n = len(o["updated"])
            return {"kind": "updated", "text": f"{n} Raspberry Pi OS update{'s' if n != 1 else ''} installed."}
        return None

    def view(self) -> dict:
        j = self.admin.update_view()
        o = self.os_view()
        k = self.keypad_view()
        if self._system is None:
            self._system = system_versions()
        return {"auto": j["auto"], "juk3box": j, "os": o, "keypad": k, "included": included(),
                "system": self._system, "announce": self.announcement(j, o, k)}

    def quiet(self) -> bool:
        """Nothing playing, from any source."""
        live = self.admin.live
        return not any(isinstance(live.get(k), dict) and live[k].get("status") == "playing" for k in NOW_PLAYING)

    async def run(self) -> None:
        """The overnight restart, when updates need one and the owner left them automatic."""
        while True:
            await asyncio.sleep(RESTART_CHECK_S)
            try:
                if (self.admin.update_view()["auto"] and self.os_view()["restart_needed"]
                        and time.localtime().tm_hour in RESTART_HOURS and self.quiet()):
                    self.admin.log.info("updates: restarting overnight to finish installing")
                    await self.admin.reboot()
            except Exception as e:  # never let this stop the admin
                self.admin.log.warning("updates: %s", e)
