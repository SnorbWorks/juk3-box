"""jb-netwatch: keeps the jukebox online.

Every 30 seconds it looks at the network (NetworkManager's state, the
router answering a ping, the kernel log for a hung Wi-Fi driver) and does
what policy.py says: nothing, reconnect, turn the Wi-Fi off and on, or
restart the jukebox. Every action is written to the journal and to
/var/lib/juk3-box/netwatch/events.jsonl.

Needs no bus and nothing else of ours, so it keeps working whatever else
has stopped. Every command has a time limit: a hung driver can hang the
network tools too. Runs as root (it reconnects and reboots), via
deploy/systemd/jb-netwatch.service.
"""

import json
import logging
import subprocess
import time
from pathlib import Path

from services.jb_netwatch import policy
from shared.files import write_atomic

CHECK_EVERY_S = 30
STATE_DIR = Path("/var/lib/juk3-box/netwatch")
REBOOTS_FILE = STATE_DIR / "reboots.json"
EVENTS_FILE = STATE_DIR / "events.jsonl"
KEEP_EVENTS = 500
# If the graceful reboot hasn't happened by then (a hung driver can stall
# shutdown), restart at once.
FORCE_REBOOT_AFTER_S = 180
# What's playing, from the phone or a USB drive (both report every few
# seconds while a song plays).
NOW_PLAYING_TOPICS = ["jukebox/jb-source-phone/now-playing", "jukebox/jb-source-local/now-playing"]

log = logging.getLogger("jb-netwatch")


def run(args: list[str], timeout: float) -> tuple[int, str]:
    """A command's exit code and output; 124 if it ran out of time."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as e:
        return 127, str(e)


def uptime() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 0.0


def nm_state() -> str | None:
    code, out = run(["nmcli", "-t", "-f", "STATE", "general"], 10)
    return out.strip() if code == 0 else None


def default_route() -> tuple[str | None, str | None]:
    """The router's address and the interface to it."""
    code, out = run(["ip", "-4", "route", "show", "default"], 5)
    parts = out.split() if code == 0 else []
    gateway = parts[parts.index("via") + 1] if "via" in parts else None
    dev = parts[parts.index("dev") + 1] if "dev" in parts else None
    return gateway, dev


def ping(host: str) -> bool:
    return run(["ping", "-c", "2", "-W", "2", host], 10)[0] == 0


def kernel_log() -> str:
    return run(["journalctl", "-k", "-b", "-o", "cat", "--no-pager"], 20)[1]


def playing() -> bool:
    code, out = run(["mosquitto_sub", "-h", "localhost", *(a for t in NOW_PLAYING_TOPICS for a in ("-t", t)),
                     "-C", "1", "-W", "3"], 6)
    try:
        return code == 0 and json.loads(out).get("status") == "playing"
    except (ValueError, AttributeError):
        return False


def wifi_device() -> str | None:
    code, out = run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], 10)
    for line in out.splitlines() if code == 0 else []:
        dev, _, kind = line.partition(":")
        if kind == "wifi":
            return dev
    return None


def load_reboots() -> list[float]:
    try:
        return [float(t) for t in json.loads(REBOOTS_FILE.read_text())]
    except (OSError, ValueError, TypeError):
        return []


def save_reboots(reboots: list[float]) -> None:
    write_atomic(REBOOTS_FILE, json.dumps(reboots))


def record(event: str, **details) -> None:
    log.warning("%s %s", event, details or "")
    try:
        lines = EVENTS_FILE.read_text().splitlines()[-(KEEP_EVENTS - 1):] if EVENTS_FILE.exists() else []
        lines.append(json.dumps({"at": time.time(), "event": event, **details}))
        write_atomic(EVENTS_FILE, "\n".join(lines) + "\n")
    except OSError as e:
        log.error("can't write %s: %s", EVENTS_FILE, e)


def reboot(pol: policy.Policy, hung: bool) -> None:
    pol.rebooted(time.time())
    save_reboots(pol.reboots)
    record("reboot", driver_hung=hung, uptime=round(uptime()))
    run(["sync"], 60)
    run(["systemctl", "reboot"], 30)
    time.sleep(FORCE_REBOOT_AFTER_S)
    # Still here: shutdown is stuck (a process wedged in the hung driver).
    record("reboot-forced")
    run(["sync"], 60)
    Path("/proc/sysrq-trigger").write_text("b")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    pol = policy.Policy(reboots=load_reboots())
    gateway_ever_answered = False
    last_action = policy.OK
    log.info("watching the network every %ds", CHECK_EVERY_S)
    while True:
        gateway, _ = default_route()
        ping_ok = bool(gateway) and ping(gateway)
        gateway_ever_answered |= ping_ok
        online = policy.is_online(nm_state(), gateway, ping_ok, gateway_ever_answered)
        hung = not online and policy.driver_hung(kernel_log())
        action = pol.step(time.time(), uptime(), online, hung, playing() if not online else False)

        if action == policy.OK and last_action != policy.OK:
            record("back-online")
        elif action == policy.RECONNECT:
            dev = wifi_device()
            record("reconnect", device=dev)
            if dev:
                run(["nmcli", "device", "connect", dev], 45)
        elif action == policy.RADIO:
            record("wifi-off-on")
            run(["nmcli", "radio", "wifi", "off"], 20)
            time.sleep(3)
            run(["nmcli", "radio", "wifi", "on"], 20)
        elif action == policy.DEFER and last_action != policy.DEFER:
            record("reboot-waits-for-music")
        elif action == policy.REBOOT:
            reboot(pol, hung)
        elif action == policy.WAIT and last_action == policy.OK:
            record("offline", nm=nm_state(), gateway=gateway, driver_hung=hung)
        last_action = action
        time.sleep(CHECK_EVERY_S)


if __name__ == "__main__":
    main()
