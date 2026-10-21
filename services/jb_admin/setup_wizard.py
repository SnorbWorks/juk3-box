"""The setup wizard (docs/Setup Wizard — Plan.md): first boot, or re-run
from the admin. One question per screen on a phone or computer (/setup),
with the cabinet showing progress and doing the hands-on checks.

Each step detects what it can, asks only what it can't read, changes
things, and checks the result. Progress and answers are kept on the Pi
(/var/lib/juk3-box/setup.json) as each step completes, so a reload, a
dropped phone or the one restart in "apply" carries on where it was.
Steps 6-9 only gather answers; "apply" makes the changes that need a
restart all at once. The privileged steps go through deploy/jb-setupctl
(root, started by systemd on a request; no sudo).

The cabinet's part: jukebox/jb-admin/setup (retained), read by jb-ui's
setup overlay; its yes/no answers come back on jukebox/jb-admin/setup-answer.
"""

import asyncio
import math
import re
import socket
import struct
import tempfile
import time
import wave
from pathlib import Path

from services.jb_admin.files import read_json, write_json
from services.jb_lights import present
from shared import hardware

SETUP_TOPIC = "jukebox/jb-admin/setup"
ANSWER_TOPIC = "jukebox/jb-admin/setup-answer"
PAGE_SEEN_S = 120          # the cabinet shows setup while a page has asked this recently
LOAD_TEST_S = 30
IDENTIFY_S = 120
PASS_VOLTS = 4.95          # the load test passes at or above this (the Oct 20 test: 5.14 V)
TEST_VOLUME = 0.05         # the Oct 20 test volume
RTC_CHARGE = "dtparam=rtc_bbat_vchg=3000000"
CONFIG_TXT = Path("/boot/firmware/config.txt")
PORT = 8080

CHAIN_NAMES = {"buttons": "Button lights", "accent": "Upper panel and footlights", "levelbar": "Volume bar"}
CHAIN_LOOK = {
    "buttons": ("Are the buttons lit, all of them?",
                "Every button should glow soft white, with one bright spot walking along them in order."),
    "accent": ("Is the upper panel lit all the way around?",
               "It should be soft white end to end, footlights included, with a bright spot running along it."),
    "levelbar": ("Is the volume bar lit?",
                 "It should be soft white, with a bright stripe walking from one end to the other."),
}
CHAIN_TIPS = {
    "buttons": ["On a Pi 5 the buttons' signal is inverted by the board itself: if they show random colours, that's the first suspect.",
                "Check the button chain's plug on the light board."],
    "accent": ["A dark stretch that starts at the same LED every time is a loose joint just before it.",
               "If the volume bar lit instead, the two light lines are swapped at the board."],
    "levelbar": ["If it stops at the same strip every time, the joint right after the last lit strip is loose.",
                 "If the upper panel lit instead, the two light lines are swapped at the board."],
}


class WizardError(Exception):
    """A friendly message for the page."""


class System:
    """What the wizard reads off the Pi. Tests replace it."""

    async def run(self, *cmd: str, timeout: float = 10, env: dict | None = None) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            return out.decode(errors="replace") if proc.returncode == 0 else ""
        except (OSError, asyncio.TimeoutError):
            return ""

    def read(self, path: str) -> bytes | None:
        try:
            return Path(path).read_bytes()
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    async def volts(self) -> float | None:
        m = re.search(r"([\d.]+)V", await self.run("vcgencmd", "pmic_read_adc", "EXT5V_V"))
        return round(float(m.group(1)), 2) if m else None

    async def throttled(self) -> int | None:
        m = re.search(r"0x([0-9a-fA-F]+)", await self.run("vcgencmd", "get_throttled"))
        return int(m.group(1), 16) if m else None

    async def temp(self) -> float | None:
        m = re.search(r"([\d.]+)'C", await self.run("vcgencmd", "measure_temp"))
        return float(m.group(1)) if m else None

    def fan_rpm(self) -> int | None:
        for p in Path("/sys/devices/platform/cooling_fan/hwmon").glob("*/fan1_input"):
            data = self.read(str(p))
            if data is not None and data.strip().isdigit():
                return int(data.strip())
        return None

    def boot_id(self) -> str:
        return (self.read("/proc/sys/kernel/random/boot_id") or b"").decode().strip()

    def serial(self) -> str:
        return (self.read("/proc/device-tree/serial-number") or b"").decode(errors="replace").strip("\0 \n")

    def memory_gb(self) -> int | None:
        m = re.search(rb"MemTotal:\s+(\d+)", self.read("/proc/meminfo") or b"")
        return max(1, round(int(m.group(1)) / 1024 / 1024)) if m else None

    def pd_offers(self) -> bool:
        """A USB-C charger offered power over USB-PD at boot (the Pi 5)."""
        return any(self.read("/proc/device-tree/chosen/power/usbpd_power_data_objects") or b"")

    def max_current(self) -> int | None:
        data = self.read("/proc/device-tree/chosen/power/max_current")
        return struct.unpack(">I", data[:4])[0] if data and len(data) >= 4 else None

    async def eeprom_psu(self) -> str | None:
        m = re.search(r"^PSU_MAX_CURRENT=(\d+)", await self.run("rpi-eeprom-config"), re.M)
        return m.group(1) if m else None

    def rtc_charging(self) -> bool:
        text = (self.read(str(CONFIG_TXT)) or b"").decode(errors="replace")
        return any(line.strip() == RTC_CHARGE for line in text.splitlines())

    async def network(self) -> dict:
        """{"via": "wifi"|"ethernet"|None, "name": connection or SSID}."""
        out = await self.run("nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device")
        found = {"via": None, "name": None}
        for line in out.splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[1] == "connected" and parts[0] in ("wifi", "ethernet"):
                if found["via"] != "ethernet":
                    found = {"via": parts[0], "name": parts[2]}
        return found

    async def timezone(self) -> dict:
        tz = (await self.run("timedatectl", "show", "-p", "Timezone", "--value")).strip()
        synced = (await self.run("timedatectl", "show", "-p", "NTPSynchronized", "--value")).strip() == "yes"
        country = (await self.run("raspi-config", "nonint", "get_wifi_country")).strip()
        return {"timezone": tz, "synced": synced, "country": country}

    async def sound(self) -> dict:
        cards = await self.run("aplay", "-l")
        threads = await self.run("ps", "-eLo", "cls,rtprio,comm")
        return {"dac": "DAC" in cards,
                "realtime": any(l.split()[:1] == ["FF"] and "data-loop" in l for l in threads.splitlines()),
                "clock": self.exists("/home/admin/.config/pipewire/pipewire.conf.d/10-jukebox-clock.conf")}

    async def addresses(self) -> list[str]:
        return [a for a in (await self.run("hostname", "-I")).split() if "." in a]


def chime(path: Path) -> None:
    """A short rising three-note chime, for "Do you hear it?"."""
    rate, out = 44100, bytearray()
    for i, freq in enumerate((523.25, 659.25, 783.99)):
        n = int(rate * (0.5 if i < 2 else 0.9))
        for k in range(n):
            env = min(1.0, k / 400) * math.exp(-3.0 * k / n)
            out += struct.pack("<h", int(12000 * env * math.sin(2 * math.pi * freq * k / rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(out))


def qr_rows(text: str) -> list[str]:
    """The QR code as rows of "1"/"0" (dark/light), for the cabinet to draw.
    Empty if segno isn't installed."""
    try:
        import segno
    except ImportError:
        return []
    q = segno.make(text, error="m")
    return ["".join("1" if c else "0" for c in row) for row in q.matrix]


# The steps ------------------------------------------------------------------------

class Step:
    id = ""
    title = ""
    short = ""

    def applies(self, w) -> bool:
        return True

    async def view(self, w) -> dict:
        return {}

    async def submit(self, w, data: dict) -> dict:
        """Check and act on the answer; returns what's kept in setup.json."""
        return {}

    async def action(self, w, name: str, data: dict) -> dict:
        raise WizardError("That isn't something this step does.")

    def cabinet(self, w) -> str:
        """The line under the step's title on the cabinet."""
        return "Answer on your phone or computer."


class Password(Step):
    id, title, short = "password", "Choose a password", "Password"

    async def view(self, w):
        return {"needed": not w.admin.auth.has_password()}

    async def submit(self, w, data):
        if not w.admin.auth.has_password():
            raise WizardError("Choose a password first.")
        return {}


class Name(Step):
    id, title, short = "name", "Name your jukebox", "Name"

    async def view(self, w):
        current = (w.admin.live.get("helper_settings") or {}).get("name")
        return {"name": (w.answer("name").get("name") or current or "Juk3box")}

    async def submit(self, w, data):
        name = data.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
            raise WizardError("A name of 1 to 40 characters, please.")
        name = name.strip()
        await w.admin.rename_jukebox(name)
        # The name a phone sees over Bluetooth (BlueZ keeps it).
        await w.system.run("bluetoothctl", "system-alias", name)
        return {"name": name}


class Wifi(Step):
    id, title, short = "wifi", "Connect to Wi-Fi", "Wi-Fi"

    async def view(self, w):
        return await w.system.network()

    async def submit(self, w, data):
        net = await w.system.network()
        if not net["via"]:
            raise WizardError("The jukebox isn't on a network yet.")
        return net


class Time(Step):
    id, title, short = "time", "Time and region", "Time"

    async def view(self, w):
        return await w.system.timezone()

    async def submit(self, w, data):
        now = await w.system.timezone()
        tz, cc = data.get("timezone"), data.get("country")
        if not isinstance(tz, str) or not re.fullmatch(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}", tz):
            raise WizardError("Pick a time zone.")
        if not isinstance(cc, str) or not re.fullmatch(r"[A-Z]{2}", cc):
            raise WizardError("Pick a country.")
        if tz != now["timezone"]:
            await w.ctl("timezone", tz)
        if cc != now["country"]:
            await w.ctl("wifi-country", cc)
        return {"timezone": tz, "country": cc}


class Board(Step):
    id, title, short = "board", "Your Raspberry Pi", "Board"

    async def view(self, w):
        b = w.board
        return {"model": b.get("model"), "family": b.get("family"), "memory_gb": w.system.memory_gb(),
                "supported": b.get("family") in ("pi4", "pi5")}

    async def submit(self, w, data):
        return {"family": w.board.get("family"), "model": w.board.get("model"), "serial": w.system.serial()}


class Power(Step):
    id, title, short = "power", "How is it powered?", "Power"

    async def view(self, w):
        pi5 = w.board.get("family") == "pi5"
        v = {"pi5": pi5, "throttled": await w.system.throttled()}
        if pi5:
            v.update(pd=w.system.pd_offers(), max_current=w.system.max_current(),
                     eeprom=await w.system.eeprom_psu(), source=w.answer("power").get("source"))
        return v

    async def submit(self, w, data):
        if w.board.get("family") != "pi5":
            return {"source": "board"}
        if w.system.pd_offers():
            return {"source": "usbc-pd"}
        source = data.get("source")
        if source not in ("gpio", "usbc"):
            raise WizardError("Pick how the Pi is powered.")
        return {"source": source}


class Battery(Step):
    id, title, short = "battery", "Clock battery", "Clock"

    def applies(self, w):
        return w.board.get("family") == "pi5"

    async def view(self, w):
        return {"rtc": w.system.exists("/dev/rtc0"), "charging": w.system.rtc_charging(),
                "official": w.answer("battery").get("official")}

    async def submit(self, w, data):
        if not isinstance(data.get("official"), bool):
            raise WizardError("Pick yes or no.")
        return {"official": data["official"]}


class Cooling(Step):
    id, title, short = "cooling", "Cooling", "Cooling"

    def applies(self, w):
        return w.board.get("family") == "pi5"

    async def view(self, w):
        return {"fan": w.system.fan_rpm() is not None, "rpm": w.system.fan_rpm(),
                "temp": await w.system.temp(), "spin": w.answer("cooling").get("spin")}

    async def action(self, w, name, data):
        if name != "spin":
            return await super().action(w, name, data)
        if w.system.fan_rpm() is None:
            raise WizardError("No fan found on the FAN connector.")
        spin = {}
        try:
            for level in ("2", "4"):
                await w.ctl("fan", level)
                await asyncio.sleep(5)
                spin[level] = w.system.fan_rpm()
        finally:
            await w.ctl("fan", "auto")
        w.remember("cooling", {"spin": spin})
        return {"spin": spin}

    async def submit(self, w, data):
        return {"fan": w.system.fan_rpm() is not None, **w.answer("cooling")}


class Lights(Step):
    id, title, short = "lights", "Which lights does it have?", "Lights"

    async def view(self, w):
        return {"chains": present.read(w.lights_path), "names": CHAIN_NAMES}

    async def submit(self, w, data):
        chains = {name: data.get(name) is True for name in present.CHAINS}
        write_json(w.lights_path, chains)
        await w.admin.restart_unit("jb-lights")
        return chains


class Apply(Step):
    id, title, short = "apply", "Apply your settings", "Apply"

    async def changes(self, w) -> list[dict]:
        """What needs the restart: [{"verb", "arg", "why"}], only those that differ."""
        out = []
        if w.board.get("family") == "pi5":
            source = w.answer("power").get("source")
            want = "5000" if source == "gpio" else "unset" if source in ("usbc", "usbc-pd") else None
            have = await w.system.eeprom_psu()
            if want and want != (have or "unset"):
                out.append({"verb": "psu", "arg": want, "why": "Power through the pins: the Pi may draw its full 5 A"
                            if want == "5000" else "Power from a USB-C charger: the Pi asks the charger what it can give"})
            if "official" in w.answer("battery"):
                charge = w.answer("battery")["official"] is True
                if charge != w.system.rtc_charging():
                    out.append({"verb": "rtc-charge", "arg": "on" if charge else "off",
                                "why": "Charge the clock battery" if charge else "Stop charging the clock battery"})
        return out

    async def view(self, w):
        restart = w.state.get("restart") or {}
        return {"changes": [c["why"] for c in await self.changes(w)], "restarting": bool(restart),
                "verified": w.answer("apply").get("verified")}

    async def submit(self, w, data):
        changes = await self.changes(w)
        if not changes:
            return {"restarted": False, "verified": await w.verify()}
        for c in changes:
            await w.ctl(c["verb"], c["arg"])
        w.state["restart"] = {"boot_id": w.system.boot_id(), "at": time.time(), "changes": [c["why"] for c in changes]}
        w.save()
        w.later(4, w.admin.reboot)
        raise Restarting()

    def cabinet(self, w):
        return "Restarting to apply your settings…" if w.state.get("restart") else super().cabinet(w)


class Restarting(Exception):
    """Apply's submit: the Pi is about to restart; the page waits."""


class LightCheck(Step):
    id, title, short = "lightcheck", "Check the lights", "Check lights"

    def chains(self, w):
        on = present.read(w.lights_path)
        return [c for c in present.CHAINS if on[c]]

    def applies(self, w):
        return bool(self.chains(w))

    async def view(self, w):
        return {"chains": [{"id": c, "name": CHAIN_NAMES[c], "ask": CHAIN_LOOK[c][0], "detail": CHAIN_LOOK[c][1],
                            "tips": CHAIN_TIPS[c]} for c in self.chains(w)],
                "showing": w.showing, "answers": w.answer("lightcheck"),
                "cabinet_answer": w.cabinet_answer("lightcheck")}

    async def action(self, w, name, data):
        if name == "show":
            chain = data.get("chain")
            if chain not in self.chains(w):
                raise WizardError("Which lights?")
            await w.ctl("lights", "identify", chain, str(IDENTIFY_S))
            w.showing = chain
            w.asked_at = time.time()
            w.answers_in.pop("lightcheck", None)
            w.publish()
            return {"showing": chain}
        if name == "answer":
            chain, ok = data.get("chain"), data.get("ok")
            if chain not in self.chains(w) or not isinstance(ok, bool):
                raise WizardError("Which lights?")
            w.remember("lightcheck", {chain: ok})
            return {"answers": w.answer("lightcheck")}
        if name == "absent":
            chain = data.get("chain")
            if chain in present.CHAINS:
                chains = present.read(w.lights_path)
                chains[chain] = False
                write_json(w.lights_path, chains)
                w.remember("lights", chains)
                await w.admin.restart_unit("jb-lights")
            return {"chains": self.chains(w)}
        return await super().action(w, name, data)

    async def submit(self, w, data):
        await w.stop_lights()
        return w.answer("lightcheck")

    def cabinet(self, w):
        if w.showing:
            return f"Look at the {CHAIN_NAMES[w.showing].lower()} now. Answer on your phone, or turn and press the knob."
        return super().cabinet(w)

    def ask(self, w):
        if w.showing:
            return {"id": f"lightcheck-{w.asked_at}", "q": CHAIN_LOOK[w.showing][0],
                    "yes": "Yes, they look right", "no": "No, something's off"}
        return None


class LoadTest(Step):
    id, title, short = "loadtest", "Power check", "Power check"

    async def view(self, w):
        return {"pi5": w.board.get("family") == "pi5", "test": w.load_test, "result": w.answer("loadtest") or None,
                "chains": [c for c, on in present.read(w.lights_path).items() if on]}

    async def action(self, w, name, data):
        if name != "start":
            return await super().action(w, name, data)
        if w.load_test and w.load_test.get("running"):
            return {"test": w.load_test}
        await w.start_load_test()
        return {"test": w.load_test}

    async def submit(self, w, data):
        t = w.load_test or {}
        if t.get("running"):
            raise WizardError("The check is still running.")
        if "passed" not in t and not data.get("skip"):
            raise WizardError("Run the check first.")
        return {k: t.get(k) for k in ("passed", "min_volts", "max_temp", "throttled")} if "passed" in t else {"skipped": True}

    def cabinet(self, w):
        t = w.load_test or {}
        if t.get("running"):
            v = t.get("min_volts")
            return f"Every light at full power for {LOAD_TEST_S} seconds." + (f" Lowest so far: {v:.2f} V" if v else "")
        return super().cabinet(w)


class Sound(Step):
    id, title, short = "sound", "Sound check", "Sound"

    async def view(self, w):
        return {**await w.system.sound(), "volume": (w.admin.live.get("volume") or {}).get("volume"),
                "played": w.played, "cabinet_answer": w.cabinet_answer("sound")}

    async def action(self, w, name, data):
        if name != "play":
            return await super().action(w, name, data)
        await w.play_chime()
        return {"played": True}

    async def submit(self, w, data):
        if not isinstance(data.get("heard"), bool):
            raise WizardError("Did you hear it?")
        return {"heard": data["heard"], **await w.system.sound()}

    def cabinet(self, w):
        return "Listen for a short chime. Answer on your phone, or with the knob." if w.played else super().cabinet(w)

    def ask(self, w):
        return {"id": f"sound-{w.asked_at}", "q": "Do you hear the chime?", "yes": "Yes", "no": "No"} if w.played else None


class Screen(Step):
    id, title, short = "screen", "The screen", "Screen"

    async def view(self, w):
        return {"display": w.admin.live.get("display")}

    def cabinet(self, w):
        return "Check that everything fits inside the cabinet's glass."


class Knob(Step):
    id, title, short = "knob", "Knob and keys", "Knob"

    async def view(self, w):
        return {"input": w.input_seen, "device": (w.admin.live.get("input") or {}).get("device")}

    async def action(self, w, name, data):
        if name == "reset":
            w.input_seen = {"turn": False, "click": False, "keys": []}
            return {"input": w.input_seen}
        return await super().action(w, name, data)

    def cabinet(self, w):
        return "Turn the knob, press it, then press a few letter and number keys."


class Sources(Step):
    id, title, short = "sources", "Your music", "Music"

    async def view(self, w):
        live = w.admin.live
        return {"bluetooth": len((live.get("phones") or {}).get("phones") or []),
                "app": len((live.get("helper") or {}).get("phones") or []),
                "drives": len((live.get("drives_in") or {}).get("drives") or []),
                "pairing": (live.get("pairing") or {}).get("state")}

    async def action(self, w, name, data):
        if name == "bluetooth":
            await w.admin.bus_cmd("jukebox/jb-source-phone/cmd", {"action": "pair_start"})
            return {"started": True}
        if name == "app":
            await w.admin.bus_cmd("jukebox/jb-source-helper/cmd", {"action": "settings", "allow_pairing": True})
            return {"started": True}
        return await super().action(w, name, data)


class WholeHome(Step):
    id, title, short = "wholehome", "Music around the house", "House"

    async def view(self, w):
        vol = w.admin.live.get("volume") or {}
        return {"speakers": (w.admin.live.get("airplay") or {}).get("receivers") or [],
                "whole_home": vol.get("whole_home") or {},
                "caution": hardware.feature_suggestion("whole_home", w.board.get("family"))}

    async def submit(self, w, data):
        zone = data.get("speaker")
        if zone is None:
            await w.admin.bus_cmd("jukebox/jb-audio/cmd", {"action": "whole_home", "enabled": False})
            return {"speaker": None}
        if not isinstance(zone, str) or not 0 < len(zone) <= 100:
            raise WizardError("Which speaker?")
        await w.admin.bus_cmd("jukebox/jb-audio/cmd", {"action": "whole_home", "enabled": True, "airplay": zone})
        return {"speaker": zone}


class Suggestions(Step):
    id, title, short = "suggestions", "Suggestions for your Pi", "Suggestions"

    def items(self, w) -> list[dict]:
        family = w.board.get("family")
        out = []
        for m in w.admin.registry.view():
            s = hardware.module_suggestion(m, family)
            if s:
                out.append({"id": m["id"], "kind": "module", "name": m.get("name") or m["id"], **s})
        if not any(present.read(w.lights_path).values()) and not any(i["id"] == "jb-analyzer" for i in out):
            out.append({"id": "jb-analyzer", "kind": "module", "name": "Music analyzer",
                        "why": "It mostly feeds the light show, and this jukebox has no lights.", "cost": None})
        for feature, s in (hardware.FEATURES.get(family) or {}).items():
            if not s.get("warn"):
                out.append({"id": feature, "kind": "feature", "name": feature.replace("_", " ").capitalize(), **s})
        return out

    def applies(self, w):
        return bool(self.items(w))

    async def view(self, w):
        return {"items": self.items(w), "family": w.board.get("family")}

    async def submit(self, w, data):
        keep_on = data.get("on") if isinstance(data.get("on"), list) else []
        off = []
        for item in self.items(w):
            if item["kind"] == "module" and item["id"] not in keep_on:
                await w.admin.set_module_enabled(item["id"], False)
                off.append(item["id"])
        return {"off": off}


class Finish(Step):
    id, title, short = "finish", "All set", "Done"

    async def view(self, w):
        return {"summary": w.summary()}

    async def submit(self, w, data):
        w.state["done"] = True
        w.state["finished_at"] = time.time()
        return {}

    def cabinet(self, w):
        return "Setup is finished. Enjoy your jukebox!"


STEPS = [Password(), Name(), Wifi(), Time(), Board(), Power(), Battery(), Cooling(), Lights(), Apply(),
         LightCheck(), LoadTest(), Sound(), Screen(), Knob(), Sources(), WholeHome(), Suggestions(), Finish()]
BY_ID = {s.id: s for s in STEPS}
SKIPPABLE = {"screen", "knob", "sources", "wholehome"}


class Wizard:
    def __init__(self, admin, path: Path, lights_path: Path = present.PATH, system: System | None = None):
        self.admin = admin
        self.path = Path(path)
        self.lights_path = Path(lights_path)
        self.system = system or System()
        self.board = admin.board
        self.log = admin.log
        self.state = self.load()
        self.page_seen = 0.0
        self.showing = None                 # the chain the cabinet is lighting for the check
        self.load_test = None
        self.played = False
        self.asked_at = 0.0                 # when the open yes/no was asked: the cabinet's ask id
        self.answers_in: dict[str, dict] = {}     # the cabinet's yes/no answers, by step
        self.input_seen = {"turn": False, "click": False, "keys": []}
        self._published = None
        self._viewing = None
        self._tasks = set()

    # State -------------------------------------------------------------------------

    def load(self) -> dict:
        data = read_json(self.path, {})
        data = data if isinstance(data, dict) else {}
        data.setdefault("answers", {})
        data.setdefault("completed", [])
        return data

    def save(self) -> None:
        write_json(self.path, self.state)

    def answer(self, step_id: str) -> dict:
        a = self.state["answers"].get(step_id)
        return a if isinstance(a, dict) else {}

    def remember(self, step_id: str, values: dict) -> None:
        self.state["answers"][step_id] = {**self.answer(step_id), **values}
        self.save()

    @property
    def done(self) -> bool:
        return bool(self.state.get("done"))

    def steps(self) -> list[Step]:
        return [s for s in STEPS if s.applies(self)]

    def completed(self, step: Step) -> bool:
        if step.id == "password":
            return self.admin.auth.has_password()
        return step.id in self.state["completed"]

    def current(self) -> Step:
        for s in self.steps():
            if not self.completed(s):
                return s
        return BY_ID["finish"]

    def after(self, step_id: str) -> Step:
        steps = self.steps()
        ids = [s.id for s in steps]
        if step_id in ids and ids.index(step_id) + 1 < len(ids):
            return steps[ids.index(step_id) + 1]
        return BY_ID["finish"]

    def before(self, step_id: str) -> Step | None:
        steps = self.steps()
        ids = [s.id for s in steps]
        return steps[ids.index(step_id) - 1] if step_id in ids and ids.index(step_id) > 0 else None

    # The page ------------------------------------------------------------------------

    async def view(self, step_id: str | None = None) -> dict:
        self.page_seen = time.monotonic()
        await self.check_restart()
        if not self.admin.auth.has_password():
            step = BY_ID["password"]
        else:
            step = BY_ID.get(step_id) if step_id else None
            if step is None or not step.applies(self) or step.id == "password":
                step = self.current()
                if step.id == "password":
                    step = self.after("password")
        steps = self.steps()
        view = {
            "done": self.done,
            "steps": [{"id": s.id, "title": s.title, "short": s.short, "done": self.completed(s)}
                      for s in steps if s.id != "password"],
            "step": {"id": step.id, "title": step.title, "skippable": step.id in SKIPPABLE,
                     "back": (b.id if (b := self.before(step.id)) and b.id != "password" else None),
                     **await step.view(self)},
            "board": {"family": self.board.get("family"), "model": self.board.get("model")},
        }
        self._viewing = step.id
        self.publish()
        return view

    async def submit(self, step_id: str, data: dict) -> dict:
        step = BY_ID.get(step_id)
        if step is None or not step.applies(self):
            raise WizardError("That step isn't part of this setup.")
        if data.get("skip") and step.id in SKIPPABLE:
            kept = {"skipped": True}
        else:
            try:
                kept = await step.submit(self, data)
            except Restarting:
                self.publish()
                return {"restarting": True}
        self.state["answers"][step.id] = {**self.answer(step.id), **kept}
        if step.id not in self.state["completed"]:
            self.state["completed"].append(step.id)
        self.save()
        self.log.info("setup: %s done", step.id)
        if step.id == "finish":
            self.publish()
            return {"done": True}
        return await self.view(self.after(step.id).id)

    async def action(self, step_id: str, name: str, data: dict) -> dict:
        step = BY_ID.get(step_id)
        if step is None or not step.applies(self):
            raise WizardError("That step isn't part of this setup.")
        self.page_seen = time.monotonic()
        out = await step.action(self, name, data)
        self.publish()
        return out

    async def restart_wizard(self) -> None:
        """Run it again from the admin: answers are kept as the starting
        point, every step is asked again."""
        self.state["completed"] = []
        self.state["done"] = False
        self.save()

    # Changes and checks ----------------------------------------------------------------

    async def ctl(self, verb: str, *args: str) -> str:
        return await self.admin.setup_ctl(verb, *args)

    def later(self, delay: float, fn) -> None:
        async def go():
            await asyncio.sleep(delay)
            await fn()
        task = asyncio.get_running_loop().create_task(go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def verify(self) -> dict:
        """After apply (and its restart): what the board says now."""
        v = {"throttled": await self.system.throttled()}
        if self.board.get("family") == "pi5":
            v.update(max_current=self.system.max_current(), eeprom=await self.system.eeprom_psu(),
                     rtc_charging=self.system.rtc_charging())
        return v

    async def check_restart(self) -> None:
        """Back from apply's restart: finish that step and check it took."""
        r = self.state.get("restart")
        if not r or r.get("boot_id") == self.system.boot_id():
            return
        self.state.pop("restart", None)
        self.state["answers"]["apply"] = {"restarted": True, "changes": r.get("changes"), "verified": await self.verify()}
        if "apply" not in self.state["completed"]:
            self.state["completed"].append("apply")
        self.save()

    async def stop_lights(self) -> None:
        if self.showing:
            self.showing = None
            try:
                await self.ctl("lights", "stop")
            except WizardError as e:
                self.log.warning("setup: couldn't stop the light check: %s", e)

    async def start_load_test(self) -> None:
        await self.stop_lights()
        chains = [c for c, on in present.read(self.lights_path).items() if on]
        self.load_test = {"running": True, "chains": chains, "samples": [], "min_volts": None,
                          "max_temp": None, "throttled": 0, "started": time.time()}
        if chains:
            await self.ctl("lights", "load", ",".join(chains), str(LOAD_TEST_S))
        burners = []
        if not chains:
            # No lights: load the CPU instead, so the supply is still checked.
            for _ in range(4):
                try:
                    burners.append(await asyncio.create_subprocess_exec(
                        "timeout", str(LOAD_TEST_S), "sh", "-c", "while :; do :; done"))
                except OSError:
                    pass

        async def sample():
            t = self.load_test
            try:
                for _ in range(LOAD_TEST_S):
                    await asyncio.sleep(1)
                    volts, thr, temp = await self.system.volts(), await self.system.throttled(), await self.system.temp()
                    t["samples"].append({"v": volts, "t": temp, "rpm": self.system.fan_rpm(), "thr": thr})
                    if volts is not None:
                        t["min_volts"] = volts if t["min_volts"] is None else min(t["min_volts"], volts)
                    if temp is not None:
                        t["max_temp"] = temp if t["max_temp"] is None else max(t["max_temp"], temp)
                    if thr:
                        t["throttled"] |= thr & 0xF        # now-bits only: undervoltage, capped, throttled
                    self.publish()
            finally:
                t["running"] = False
                t["passed"] = (t["throttled"] & 0x1) == 0 and (t["min_volts"] is None or t["min_volts"] >= PASS_VOLTS)
                for b in burners:
                    if b.returncode is None:
                        b.kill()
                self.publish()
        task = asyncio.get_running_loop().create_task(sample())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def play_chime(self) -> None:
        vol = (self.admin.live.get("volume") or {}).get("volume")
        if isinstance(vol, (int, float)) and abs(vol - TEST_VOLUME) > 0.005:
            await self.admin.bus_cmd("jukebox/jb-audio/cmd", {"action": "volume_delta", "value": round(TEST_VOLUME - vol, 3)})
            await asyncio.sleep(0.5)
        path = Path(tempfile.gettempdir()) / "juk3box-chime.wav"
        chime(path)
        self.played = True
        self.asked_at = time.time()
        self.answers_in.pop("sound", None)
        await self.system.run("pw-play", str(path), timeout=10, env={"XDG_RUNTIME_DIR": "/run/user/1000", "PATH": "/usr/bin:/bin"})

    def summary(self) -> list[dict]:
        a = self.state["answers"]
        rows = [{"k": "Name", "v": (a.get("name") or {}).get("name") or "Juk3box"}]
        net = a.get("wifi") or {}
        if net.get("name"):
            rows.append({"k": "Network", "v": net["name"]})
        if self.board.get("model"):
            rows.append({"k": "Board", "v": self.board["model"]})
        power = (a.get("power") or {}).get("source")
        if power:
            rows.append({"k": "Power", "v": {"gpio": "The cabinet's supply", "usbc": "A USB-C charger",
                                             "usbc-pd": "A USB-C charger", "board": "Checked"}.get(power, power)})
        chains = present.read(self.lights_path)
        rows.append({"k": "Lights", "v": ", ".join(CHAIN_NAMES[c] for c in present.CHAINS if chains[c]) or "None"})
        lt = a.get("loadtest") or {}
        if lt.get("min_volts"):
            rows.append({"k": "Power check", "v": f"{lt['min_volts']:.2f} V lowest"})
        return rows

    # The cabinet --------------------------------------------------------------------------

    def cabinet_answer(self, step_id: str):
        return self.answers_in.get(step_id)

    def heard_answer(self, payload) -> None:
        if isinstance(payload, dict) and payload.get("step") in BY_ID and isinstance(payload.get("yes"), bool):
            self.answers_in[payload["step"]] = {"yes": payload["yes"], "chain": self.showing, "at": time.time()}

    def heard_input(self, topic: str, payload) -> None:
        if not isinstance(payload, dict):
            return
        if topic.endswith("/gesture") and payload.get("control") == "knob":
            if payload.get("gesture") == "turn":
                self.input_seen["turn"] = True
            elif payload.get("gesture") == "click":
                self.input_seen["click"] = True
        elif topic.endswith("/key") and isinstance(payload.get("key"), str):
            self.input_seen["keys"] = (self.input_seen["keys"] + [payload["key"]])[-12:]

    def active(self) -> bool:
        if self.done:
            return False
        if not self.admin.auth.has_password():
            return True
        return time.monotonic() - self.page_seen < PAGE_SEEN_S

    def cabinet(self) -> dict:
        if not self.active():
            return {"active": False}
        step = BY_ID.get(self._viewing) or self.current()
        steps = [s for s in self.steps() if s.id != "password"]
        ids = [s.id for s in steps]
        address = f"http://{socket.gethostname()}.local:{PORT}/setup"
        out = {"active": True, "step": step.id, "title": step.title, "say": step.cabinet(self),
               "n": ids.index(step.id) + 1 if step.id in ids else 0, "of": len(ids),
               "steps": [{"short": s.short, "done": self.completed(s), "now": s.id == step.id} for s in steps],
               "address": address, "qr": qr_rows(address),
               "restarting": bool(self.state.get("restart"))}
        ask = step.ask(self) if hasattr(step, "ask") else None
        if ask:
            out["ask"] = ask
        if step.id == "loadtest" and self.load_test:
            t = self.load_test
            out["live"] = {"running": t.get("running"), "min_volts": t.get("min_volts"), "max_temp": t.get("max_temp"),
                           "passed": t.get("passed")}
        return out

    def publish(self) -> None:
        payload = self.cabinet()
        if payload == self._published:
            return
        self._published = payload
        self.admin.publish_soon(SETUP_TOPIC, payload, retain=True)

    async def run(self) -> None:
        """Takes the cabinet's setup screen down once the page goes quiet."""
        await self.check_restart()
        while True:
            self.publish()
            await asyncio.sleep(10)
