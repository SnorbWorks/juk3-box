import json

from services.jb_netwatch import setup_mode
from services.jb_netwatch.setup_mode import SetupMode, join_reason, parse_scan, should_start
from shared import setup_net

SCAN = "Ducks Home:80:WPA2\nDucks Home:60:WPA2\nCafe\\: Free:40:\n:70:WPA2\nJuk3box Setup ABCD:90:WPA2\n"


class FakeNM:
    """nmcli, scripted: which connections exist, and how a join goes."""

    def __init__(self, join_ok=True, join_out=""):
        self.cmds = []
        self.profiles = {"netplan-eth0": "802-3-ethernet"}
        self.join_ok, self.join_out = join_ok, join_out

    def __call__(self, args, timeout):
        self.cmds.append(args)
        if args[:4] == ["nmcli", "-t", "-f", "NAME,TYPE"]:
            return 0, "".join(f"{n}:{t}\n" for n, t in self.profiles.items())
        if "list" in args:
            return 0, SCAN
        if args[1:4] == ["connection", "add", "type"]:
            self.profiles[setup_mode.CON_NAME] = "802-11-wireless"
            return 0, ""
        if args[1:3] == ["connection", "delete"]:
            self.profiles.pop(args[3], None)
            return 0, ""
        if "connect" in args and "wifi" in args:
            ssid = args[args.index("connect") + 1]
            self.profiles[ssid] = "802-11-wireless"       # nmcli makes one either way
            return (0, "") if self.join_ok else (4, self.join_out)
        return 0, ""


class FakePortal:
    def __init__(self):
        self.on = False

    def start(self):
        self.on = True

    def stop(self):
        self.on = False


def make(tmp_path, nm=None, online=lambda: True, done=False, now=None):
    nm = nm or FakeNM()
    (tmp_path / "setup.json").write_text(json.dumps({"done": done}))
    clock = now or [1000.0]
    portal = FakePortal()
    s = SetupMode(wifi_device=lambda: "wlan0", serial=lambda: "10000000abcd1234", online=online,
                  folder=tmp_path / "setupnet", setup_json=tmp_path / "setup.json",
                  dns_conf=tmp_path / "dnsmasq-shared.d" / "juk3box-setup.conf", runner=nm,
                  portal=portal, clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    return s, nm, portal, clock


def test_it_starts_by_itself_only_on_a_jukebox_never_set_up():
    assert should_start(False, False, 30, 60, known_wifi=False)
    assert not should_start(False, False, 30, 59, known_wifi=False)       # still starting up
    assert not should_start(False, False, 30, 600, known_wifi=True)       # its Wi-Fi may be slow
    assert should_start(False, False, 180, 600, known_wifi=True)
    assert not should_start(True, False, 3600, 3600, known_wifi=False)    # set up: only when asked
    assert not should_start(False, True, 0, 600, known_wifi=False)


def test_the_network_list_is_each_network_once_strongest_first():
    nets = parse_scan(SCAN, own="Juk3box Setup ABCD")
    assert nets == [{"ssid": "Ducks Home", "signal": 80, "secure": True},
                    {"ssid": "Cafe: Free", "signal": 40, "secure": False}]


def test_a_failed_join_says_why():
    assert "password" in join_reason("Error: Connection activation failed: Secrets were required, but not provided.")
    assert "range" in join_reason("Error: No network with SSID 'x' found.")


def test_requests_round_trip_and_bad_joins_are_caught(tmp_path):
    setup_net.request("join", tmp_path, ssid="Ducks Home", password="correct horse")
    setup_net.request("scan", tmp_path)
    reqs = setup_net.take_requests(tmp_path)
    assert [r["action"] for r in reqs] == ["join", "scan"] and setup_net.take_requests(tmp_path) == []
    assert setup_net.valid_join({"ssid": "x", "password": "short"})
    assert setup_net.valid_join({"ssid": "x" * 33})
    assert setup_net.valid_join({"ssid": "Open Cafe", "password": ""}) is None
    assert setup_net.wifi_qr_text("A;B", 'p"w') == 'WIFI:T:WPA;S:A\\;B;P:p\\"w;;'


def test_starting_makes_the_hotspot_and_the_sign_in_sheet(tmp_path):
    s, nm, portal, _ = make(tmp_path, online=lambda: False)
    s.consider(online=False, offline_for=30, uptime=90)
    st = setup_net.read_state(tmp_path / "setupnet")
    assert st["active"] and st["ssid"] == "Juk3box Setup 1234" and len(st["password"]) == 8
    assert st["networks"][0]["ssid"] == "Ducks Home" and st["why"] == "not-set-up"
    assert portal.on and (tmp_path / "dnsmasq-shared.d" / "juk3box-setup.conf").read_text() == "address=/#/10.42.0.1\n"
    add = next(c for c in nm.cmds if c[1:3] == ["connection", "add"])
    assert st["password"] in add and "ap" in add and "shared" in add
    # The same name and password next time.
    s.stop("asked")
    s.start("asked")
    assert setup_net.read_state(tmp_path / "setupnet")["password"] == st["password"]


def test_a_set_up_jukebox_never_starts_it_by_itself(tmp_path):
    s, nm, portal, _ = make(tmp_path, done=True)
    s.consider(online=False, offline_for=3600, uptime=3600)
    assert not s.active and not nm.cmds


def test_joining_ends_setup_mode(tmp_path):
    s, nm, portal, _ = make(tmp_path)
    s.start("not-set-up")
    setup_net.request("join", tmp_path / "setupnet", ssid="Ducks Home", password="correct horse")
    s.tick()
    st = setup_net.read_state(tmp_path / "setupnet")
    assert not st["active"] and st["join"] == {"state": "joined", "ssid": "Ducks Home", "id": ""}
    assert setup_mode.CON_NAME not in nm.profiles and "Ducks Home" in nm.profiles
    assert not portal.on and not (tmp_path / "dnsmasq-shared.d" / "juk3box-setup.conf").exists()


def test_a_wrong_password_brings_the_hotspot_back_with_the_reason(tmp_path):
    nm = FakeNM(join_ok=False, join_out="Error: Connection activation failed: Secrets were required, but not provided.")
    s, nm, portal, _ = make(tmp_path, nm=nm)
    s.start("not-set-up")
    setup_net.request("join", tmp_path / "setupnet", ssid="Ducks Home", password="wrong horse")
    s.tick()
    st = setup_net.read_state(tmp_path / "setupnet")
    assert st["active"] and st["join"]["state"] == "failed" and "password" in st["join"]["reason"]
    assert "Ducks Home" not in nm.profiles and setup_mode.CON_NAME in nm.profiles and portal.on


def test_asked_for_and_unused_it_goes_back_after_a_while(tmp_path):
    s, nm, portal, clock = make(tmp_path, done=True)
    setup_net.request("start", tmp_path / "setupnet")
    s.tick()
    assert s.active
    clock[0] += setup_mode.REQUESTED_FOR_S + 1
    s.tick()
    assert not s.active and setup_net.read_state(tmp_path / "setupnet")["ended"] == "unused"
    assert ["nmcli", "device", "connect", "wlan0"] in nm.cmds
