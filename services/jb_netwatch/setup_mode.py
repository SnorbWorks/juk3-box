"""Setup mode: the jukebox's own Wi-Fi, so a phone can set it up when it
has no network of its own (docs/Setup Wizard — Plan.md, "Setup mode").

jb-netwatch runs it, because offline is the point here and nothing else
should be fixing the network meanwhile. It starts by itself only on a
jukebox that has never finished setup; otherwise only when asked (the
cabinet's "Set up Wi-Fi", the admin). While it's on:

  - a NetworkManager access point, "Juk3box Setup XXXX" (the board serial's
    last four), with a password shown on the cabinet and in its QR code;
  - every name looked up on it answers with the jukebox (a dnsmasq file NM's
    shared mode reads), and anything on port 80 is sent to the setup pages,
    which is what makes a phone pop its sign-in sheet;
  - the setup pages (jb-admin) ask for a network list and a join through
    shared/setup_net.py's request files.

A join that works ends setup mode; one that fails brings the hotspot back
with the reason. Asked for on a jukebox that's already set up, it gives
up after 15 minutes with no join and goes back to its own network. A
restart always ends it too (the access point never starts by itself).
"""

import json
import logging
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shared import setup_net
from shared.files import write_atomic

CON_NAME = "juk3box-setup"
ADDRESS = "10.42.0.1"           # NetworkManager's shared mode gives itself this
SETUP_URL = f"http://{ADDRESS}:8080/setup"
DNS_CONF = Path("/etc/NetworkManager/dnsmasq-shared.d/juk3box-setup.conf")
SETUP_JSON = Path("/var/lib/juk3-box/setup.json")
CHANNEL = 6
PASSWORD_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"   # nothing that reads as another
PASSWORD_LEN = 8

# Starting by itself (never set up): this long after startup, and offline
# this long -- briefly when no Wi-Fi was ever set, longer when one was (it
# may just be slow to come back).
AUTO_AFTER_UPTIME_S = 60
AUTO_OFFLINE_NO_WIFI_S = 30
AUTO_OFFLINE_KNOWN_WIFI_S = 180
# Asked for on a set-up jukebox: back to its own network after this.
REQUESTED_FOR_S = 15 * 60
JOIN_WAIT_S = 45
ONLINE_WAIT_S = 30

log = logging.getLogger("jb-netwatch")


def run(args: list[str], timeout: float) -> tuple[int, str]:
    """A command's exit code and its output and errors together (nmcli
    explains a failed join on stderr); 124 if it ran out of time."""
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as e:
        return 127, str(e)


# Decisions (pure) ------------------------------------------------------------------

def should_start(setup_done: bool, online: bool, offline_for: float, uptime: float, known_wifi: bool) -> bool:
    if setup_done or online or uptime < AUTO_AFTER_UPTIME_S:
        return False
    return offline_for >= (AUTO_OFFLINE_KNOWN_WIFI_S if known_wifi else AUTO_OFFLINE_NO_WIFI_S)


def hotspot_name(serial: str | None) -> str:
    tail = (serial or "").strip()[-4:].upper()
    return f"Juk3box Setup {tail}" if tail else "Juk3box Setup"


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_CHARS) for _ in range(PASSWORD_LEN))


def split_terse(line: str) -> list[str]:
    """One line of nmcli -t output: fields split on ":", "\\:" kept as ":"."""
    fields, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    fields.append("".join(cur))
    return fields


def parse_scan(out: str, own: str) -> list[dict]:
    """nmcli -t -f SSID,SIGNAL,SECURITY device wifi list: each network once
    (its strongest), strongest first. Left out: hidden ones, and setup
    hotspots (ours, or another jukebox's), which are never a home network."""
    best: dict[str, dict] = {}
    for line in out.splitlines():
        f = split_terse(line)
        if len(f) < 3 or not f[0] or f[0] == own or f[0].startswith("Juk3box Setup"):
            continue
        try:
            signal = int(f[1])
        except ValueError:
            continue
        net = {"ssid": f[0], "signal": signal, "secure": f[2] not in ("", "--")}
        if f[0] not in best or signal > best[f[0]]["signal"]:
            best[f[0]] = net
    return sorted(best.values(), key=lambda n: -n["signal"])


def join_reason(out: str) -> str:
    """What to tell the owner about a join that didn't work."""
    text = out.lower()
    if "secrets were required" in text or "802-1x" in text or "psk" in text or "password" in text:
        return "That password didn't work. Check it and try again."
    if "no network with ssid" in text or "not found" in text:
        return "That network isn't in range. Move closer, or check its name."
    if "timeout" in text or "timed out" in text:
        return "The network didn't answer in time. Try again."
    return "The jukebox couldn't join that network. Try again."


# The portal ------------------------------------------------------------------------

class _ToSetup(BaseHTTPRequestHandler):
    """Every request on port 80 goes to the setup pages."""

    def _send(self):
        self.send_response(302)
        self.send_header("Location", SETUP_URL)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_HEAD = do_POST = _send

    def log_message(self, *args):
        pass


class Portal:
    def __init__(self, address: str = ADDRESS, port: int = 80):
        self.address, self.port = address, port
        self.server = None

    def start(self) -> None:
        if self.server:
            return
        try:
            self.server = ThreadingHTTPServer((self.address, self.port), _ToSetup)
        except OSError as e:
            log.warning("setup mode: no sign-in sheet (port %s: %s); the address still works", self.port, e)
            return
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="setup-portal", daemon=True).start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


# Setup mode ------------------------------------------------------------------------

class SetupMode:
    def __init__(self, *, wifi_device, serial, online, folder: Path = setup_net.DIR,
                 setup_json: Path = SETUP_JSON, dns_conf: Path = DNS_CONF, runner=run,
                 portal=None, clock=time.time, sleep=time.sleep):
        self.wifi_device, self.serial, self.online = wifi_device, serial, online
        self.folder, self.setup_json, self.dns_conf = Path(folder), Path(setup_json), Path(dns_conf)
        self.run, self.clock, self.sleep = runner, clock, sleep
        self.portal = portal or Portal()
        self.state = {"active": False}
        self.dev = None

    @property
    def active(self) -> bool:
        return bool(self.state.get("active"))

    def setup_done(self) -> bool:
        try:
            return bool(json.loads(self.setup_json.read_text()).get("done"))
        except (OSError, ValueError, AttributeError):
            return False

    def known_wifi(self) -> bool:
        code, out = self.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], 10)
        return any(f[1] == "802-11-wireless" and f[0] != CON_NAME
                   for f in (split_terse(line) for line in out.splitlines()) if len(f) >= 2)

    def write(self, **changes) -> None:
        self.state = {**self.state, **changes, "at": self.clock()}
        try:
            write_atomic(setup_net.state_path(self.folder), json.dumps(self.state), mode=0o644)
        except OSError as e:
            log.error("setup mode: can't write its state: %s", e)

    # Every check (jb-netwatch's 30 s): start by itself if it should.
    def consider(self, online: bool, offline_for: float, uptime: float) -> None:
        if self.active or online or self.setup_done():
            return
        if should_start(False, online, offline_for, uptime, self.known_wifi()):
            self.start("not-set-up")

    # Every second: requests, and giving up when asked for and unused.
    def tick(self) -> None:
        for req in setup_net.take_requests(self.folder):
            action = req["action"]
            if action == "start" and not self.active:
                self.start("asked")
            elif action == "stop" and self.active:
                self.stop("asked")
            elif action == "scan" and self.active:
                self.rescan()
            elif action == "join" and self.active:
                self.join(req)
        if (self.active and self.state.get("why") == "asked"
                and self.clock() - self.state.get("started", 0) > REQUESTED_FOR_S
                and (self.state.get("join") or {}).get("state") != "joining"):
            self.stop("unused")

    # Turning it on and off ---------------------------------------------------------

    def hotspot(self) -> tuple[str, str]:
        """The hotspot's name and password, the same every time for this board."""
        path = Path(self.folder) / "hotspot.json"
        try:
            saved = json.loads(path.read_text())
            if saved.get("ssid") and len(saved.get("password", "")) >= 8:
                return saved["ssid"], saved["password"]
        except (OSError, ValueError, AttributeError):
            pass
        ssid, password = hotspot_name(self.serial()), new_password()
        write_atomic(path, json.dumps({"ssid": ssid, "password": password}), mode=0o600)
        return ssid, password

    def scan(self) -> list[dict]:
        self.run(["nmcli", "device", "wifi", "rescan", "ifname", self.dev], 15)
        self.sleep(4)
        code, out = self.run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                              "ifname", self.dev, "--rescan", "no"], 15)
        return parse_scan(out, self.state.get("ssid", "")) if code == 0 else []

    def ap_up(self) -> bool:
        ssid, password = self.state["ssid"], self.state["password"]
        self.run(["nmcli", "connection", "delete", CON_NAME], 15)
        code, out = self.run([
            "nmcli", "connection", "add", "type", "wifi", "ifname", self.dev, "con-name", CON_NAME,
            "autoconnect", "no", "ssid", ssid,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg", "802-11-wireless.channel", str(CHANNEL),
            "ipv4.method", "shared", "ipv4.addresses", f"{ADDRESS}/24", "ipv6.method", "disabled",
            "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
            "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp"], 20)
        if code != 0:
            log.error("setup mode: can't make the hotspot: %s", out.strip())
            return False
        code, out = self.run(["nmcli", "--wait", "30", "connection", "up", CON_NAME], 40)
        if code != 0:
            log.error("setup mode: the hotspot didn't start: %s", out.strip())
            return False
        return True

    def ap_down(self) -> None:
        self.run(["nmcli", "connection", "down", CON_NAME], 20)

    def start(self, why: str) -> None:
        self.dev = self.wifi_device()
        if not self.dev:
            log.error("setup mode: no Wi-Fi to make a hotspot with")
            self.write(active=False, error="no-wifi")
            return
        ssid, password = self.hotspot()
        self.state = {"active": False, "ssid": ssid, "password": password}
        networks = self.scan()            # before the hotspot: the chip scans badly as one
        try:
            write_atomic(self.dns_conf, f"address=/#/{ADDRESS}\n", mode=0o644)
        except OSError as e:
            log.warning("setup mode: no sign-in sheet (%s)", e)
        if not self.ap_up():
            self.clean_up()
            self.write(active=False, error="hotspot")
            return
        self.portal.start()
        log.warning("setup mode on (%s): %s", why, ssid)
        self.write(active=True, why=why, started=self.clock(), address=ADDRESS, url=SETUP_URL,
                   networks=networks, scanned=self.clock(), join=None, error=None)

    def clean_up(self) -> None:
        self.portal.stop()
        self.ap_down()
        self.run(["nmcli", "connection", "delete", CON_NAME], 15)
        try:
            self.dns_conf.unlink()
        except OSError:
            pass

    def stop(self, why: str) -> None:
        self.clean_up()
        log.warning("setup mode off (%s)", why)
        self.write(active=False, ended=why)
        # Back to its own network, if it has one.
        if self.dev:
            self.run(["nmcli", "device", "connect", self.dev], 45)

    def rescan(self) -> None:
        """A fresh network list: the hotspot drops for a few seconds (phones rejoin)."""
        self.ap_down()
        networks = self.scan()
        self.run(["nmcli", "--wait", "30", "connection", "up", CON_NAME], 40)
        self.write(networks=networks, scanned=self.clock())

    # Joining the owner's network -----------------------------------------------------

    def wifi_profiles(self) -> set[str]:
        code, out = self.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], 10)
        return {f[0] for f in (split_terse(line) for line in out.splitlines())
                if len(f) >= 2 and f[1] == "802-11-wireless" and f[0] != CON_NAME}

    def join(self, req: dict) -> None:
        jid = str(req.get("id") or "")[:40]       # the page's, so it knows its own answer
        problem = setup_net.valid_join(req)
        if problem:
            self.write(join={"state": "failed", "ssid": req.get("ssid"), "reason": problem, "id": jid})
            return
        ssid, password = req["ssid"], req.get("password", "")
        self.write(join={"state": "joining", "ssid": ssid, "id": jid})
        before = self.wifi_profiles()
        self.portal.stop()
        self.ap_down()
        cmd = ["nmcli", "--wait", str(JOIN_WAIT_S), "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        cmd += ["ifname", self.dev]
        if req.get("hidden"):
            cmd += ["hidden", "yes"]
        code, out = self.run(cmd, JOIN_WAIT_S + 15)
        if code == 0 and self.wait_online():
            log.warning("setup mode: joined %s", ssid)
            self.run(["nmcli", "connection", "delete", CON_NAME], 15)
            try:
                self.dns_conf.unlink()
            except OSError:
                pass
            self.write(active=False, ended="joined", join={"state": "joined", "ssid": ssid, "id": jid})
            return
        # Didn't work: forget the half-made connection, bring the hotspot back.
        for name in self.wifi_profiles() - before:
            self.run(["nmcli", "connection", "delete", name], 15)
        reason = join_reason(out) if code != 0 else "It joined, but the router didn't answer. Try again."
        log.warning("setup mode: couldn't join %s: %s", ssid, out.strip()[:200])
        if not self.ap_up():
            self.clean_up()
            self.write(active=False, error="hotspot", join={"state": "failed", "ssid": ssid, "reason": reason, "id": jid})
            return
        self.portal.start()
        self.write(join={"state": "failed", "ssid": ssid, "reason": reason, "id": jid})

    def wait_online(self) -> bool:
        end = self.clock() + ONLINE_WAIT_S
        while self.clock() < end:
            if self.online():
                return True
            self.sleep(2)
        return False
