import asyncio
import json
import socket
import logging

from aiohttp.test_utils import TestClient, TestServer

from services.jb_admin import main, setup_wizard
from shared import selection, setup_net
from shared.files import write_atomic
from services.jb_admin.tests.test_admin import H, config


class FakePi(setup_wizard.System):
    """A GPIO-fed Pi 5 with a fan, no clock battery charging, on Wi-Fi."""

    def __init__(self):
        self.boot = "boot-1"
        self.psu = None
        self.charging = False
        self.volts_now = 5.14
        self.ran = []
        self.lastfm = None
        self.lastfm_answer = True

    async def run(self, *cmd, timeout=10, env=None):
        self.ran.append(cmd)
        return ""

    def exists(self, path):
        return path == "/dev/rtc0"

    async def volts(self):
        return self.volts_now

    async def throttled(self):
        return 0

    async def temp(self):
        return 52.0

    def fan_rpm(self):
        return 1500

    def boot_id(self):
        return self.boot

    def serial(self):
        return "abc123"

    def memory_gb(self):
        return 4

    def pd_offers(self):
        return False

    def max_current(self):
        return 5000 if self.psu == "5000" else 3000

    async def eeprom_psu(self):
        return self.psu

    def rtc_charging(self):
        return self.charging

    async def network(self):
        return {"via": "wifi", "name": "Home Wi-Fi"}

    async def timezone(self):
        return {"timezone": "America/Los_Angeles", "synced": True, "country": "US"}

    async def sound(self):
        return {"dac": True, "realtime": True, "clock": True}

    def lastfm_key(self):
        return self.lastfm

    async def lastfm_works(self, key):
        return self.lastfm_answer


def run(tmp_path, check, family="pi5", password=True, lights=None):
    async def go():
        cfg = {**config(tmp_path), "setup_path": str(tmp_path / "setup.json"),
               "lights_path": str(tmp_path / "lights.json"), "setupctl_dir": str(tmp_path / "setupctl"),
               "setupnet_dir": str(tmp_path / "setupnet")}
        if lights is not None:
            (tmp_path / "lights.json").write_text(json.dumps(lights))
        app = main.build_app(cfg, logging.getLogger("test"), bus_enabled=False)
        admin = app[main.ADMIN_KEY]
        pi = FakePi()
        admin.board = {"family": family, "model": "Raspberry Pi 5 Model B Rev 1.1"}
        w = admin.wizard
        w.system, w.board = pi, admin.board
        calls = []

        async def ctl(verb, *args, **_k):
            calls.append((verb, *args))
            if verb == "psu":
                pi.psu = None if args[0] == "unset" else args[0]
            if verb == "rtc-charge":
                pi.charging = args[0] == "on"
            if verb == "lastfm":
                pi.lastfm = None if args[0] == "clear" else args[0]
            return "ok"

        async def nothing(*_a, **_k):
            calls.append(("call", *_a))

        admin.setup_ctl = ctl
        admin.rename_jukebox = nothing
        admin.restart_unit = nothing
        admin.reboot = nothing
        admin.bus_cmd = nothing
        admin.set_module_enabled = nothing
        admin.set_module_secret = nothing
        async with TestClient(TestServer(app)) as client:
            if password:
                assert (await client.post("/api/setup", json={"password": "correct horse"}, headers=H)).status == 200
            await check(client, admin, pi, calls)
    asyncio.run(go())


async def view(client, step=None):
    r = await client.get("/api/wizard" + (f"?step={step}" if step else ""))
    assert r.status == 200, await r.text()
    return await r.json()


async def submit(client, step, data=None, status=200):
    r = await client.post(f"/api/wizard/{step}", json=data or {}, headers=H)
    assert r.status == status, await r.text()
    return await r.json()


def test_before_a_password_only_the_first_step_is_open(tmp_path):
    async def check(client, admin, pi, calls):
        v = await view(client)
        assert v["step"]["id"] == "password" and v["step"]["needed"] is True
        assert (await client.post("/api/wizard/name", json={"name": "x"}, headers=H)).status == 401
        assert (await client.get("/api/status")).status == 401
        assert (await client.get("/setup")).status == 200
        assert (await client.post("/api/setup", json={"password": "correct horse"}, headers=H)).status == 200
        assert (await view(client))["step"]["id"] == "name"
    run(tmp_path, check, password=False)


def test_a_gpio_fed_pi5_sets_the_power_and_battery_with_one_restart(tmp_path):
    async def check(client, admin, pi, calls):
        v = await submit(client, "name", {"name": "Den Jukebox"})
        assert v["step"]["id"] == "wifi" and v["step"]["name"] == "Home Wi-Fi"
        v = await submit(client, "wifi")
        v = await submit(client, "time", {"timezone": "America/Los_Angeles", "country": "US"})
        assert v["step"]["id"] == "board" and v["step"]["memory_gb"] == 4
        v = await submit(client, "board")
        assert v["step"]["id"] == "power" and v["step"]["pd"] is False
        await submit(client, "power", {}, status=400)                 # must answer: no charger spoke up
        v = await submit(client, "power", {"source": "gpio"})
        assert v["step"]["id"] == "battery"
        v = await submit(client, "battery", {"official": True})
        assert v["step"]["id"] == "cooling" and v["step"]["fan"] is True
        v = await submit(client, "cooling")
        v = await submit(client, "lights", {"buttons": True, "accent": True, "levelbar": True})
        assert v["step"]["id"] == "apply"
        assert len(v["step"]["changes"]) == 2
        out = await submit(client, "apply")
        assert out == {"restarting": True}
        assert ("psu", "5000") in calls and ("rtc-charge", "on") in calls
        await asyncio.sleep(4.2)
        assert ("call",) in calls                                      # rebooted
        # Back from the restart: apply is done and checked.
        pi.boot = "boot-2"
        v = await view(client)
        assert v["step"]["id"] == "lightcheck"
        a = json.loads((tmp_path / "setup.json").read_text())["answers"]["apply"]
        assert a["restarted"] and a["verified"]["max_current"] == 5000 and a["verified"]["rtc_charging"]
    run(tmp_path, check)


def test_nothing_to_change_means_no_restart(tmp_path):
    async def check(client, admin, pi, calls):
        pi.psu = "5000"
        for step, data in (("name", {"name": "Juk3box"}), ("wifi", {}), ("time", {"timezone": "America/Los_Angeles", "country": "US"}),
                           ("board", {}), ("power", {"source": "gpio"}), ("battery", {"official": False}),
                           ("cooling", {}), ("lights", {"buttons": True, "accent": False, "levelbar": False})):
            v = await submit(client, step, data)
        assert v["step"]["id"] == "apply" and v["step"]["changes"] == []
        v = await submit(client, "apply")
        assert v["step"]["id"] == "lightcheck"
        assert [c["id"] for c in v["step"]["chains"]] == ["buttons"]
        assert not [c for c in calls if c[0] in ("psu", "rtc-charge")]
    run(tmp_path, check)


def test_no_lights_skips_the_check_and_suggests_the_analyzer_off(tmp_path):
    async def check(client, admin, pi, calls):
        steps = [s["id"] for s in (await view(client))["steps"]]
        assert "lightcheck" not in steps and "suggestions" in steps
        v = await view(client, "suggestions")
        assert [i["id"] for i in v["step"]["items"]] == ["jb-analyzer"]
        await submit(client, "suggestions", {"on": []})
        assert ("call", "jb-analyzer", False) in calls
    run(tmp_path, check, lights={"buttons": False, "accent": False, "levelbar": False})


def test_a_pi4_left_without_the_background_video_gets_the_still_picture(tmp_path):
    async def check(client, admin, pi, calls):
        v = await view(client, "suggestions")
        assert "background_video" in [i["id"] for i in v["step"]["items"]]
        v = await submit(client, "suggestions", {"on": []})
        style = json.loads((tmp_path / "styles" / "neon" / "custom.json").read_text())
        assert style["values"]["background"] == "assets/background.jpg"
        assert json.loads((tmp_path / "setup.json").read_text())["answers"]["suggestions"]["off"][-1] == "background_video"
    run(tmp_path, check, family="pi4")


def test_a_pi4_keeping_the_background_video_keeps_it(tmp_path):
    async def check(client, admin, pi, calls):
        await submit(client, "suggestions", {"on": ["background_video"]})
        assert not (tmp_path / "styles" / "neon" / "custom.json").exists()
    run(tmp_path, check, family="pi4")


def test_a_pi4_has_no_battery_or_cooling_step_and_the_power_step_only_checks(tmp_path):
    async def check(client, admin, pi, calls):
        steps = [s["id"] for s in (await view(client))["steps"]]
        assert "battery" not in steps and "cooling" not in steps
        v = await view(client, "power")
        assert v["step"]["pi5"] is False
        await submit(client, "power")
    run(tmp_path, check, family="pi4")


def test_the_power_check_records_the_lowest_voltage(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_wizard, "LOAD_TEST_S", 2)

    async def check(client, admin, pi, calls):
        pi.volts_now = 5.02
        r = await client.post("/api/wizard/loadtest/start", json={}, headers=H)
        assert r.status == 200
        assert ("lights", "load", "buttons,accent,levelbar", "2") in calls
        await submit(client, "loadtest", {}, status=400)                # still running
        await asyncio.sleep(2.5)
        v = await view(client, "loadtest")
        assert v["step"]["test"]["passed"] is True and v["step"]["test"]["min_volts"] == 5.02
        await submit(client, "loadtest")
        assert json.loads((tmp_path / "setup.json").read_text())["answers"]["loadtest"]["min_volts"] == 5.02
    run(tmp_path, check)


def test_a_sagging_supply_fails_the_power_check(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_wizard, "LOAD_TEST_S", 1)

    async def check(client, admin, pi, calls):
        pi.volts_now = 4.80
        await client.post("/api/wizard/loadtest/start", json={}, headers=H)
        await asyncio.sleep(1.5)
        assert (await view(client, "loadtest"))["step"]["test"]["passed"] is False
    run(tmp_path, check)


def test_optional_steps_can_be_skipped_and_finishing_marks_it_done(tmp_path):
    async def check(client, admin, pi, calls):
        await submit(client, "screen", {"skip": True})
        out = await submit(client, "finish")
        assert out["done"] is True and out["address"].endswith(".local")
        assert json.loads((tmp_path / "setup.json").read_text())["done"] is True
        assert admin.wizard.cabinet() == {"active": False}
    run(tmp_path, check)


def test_the_admin_sees_where_setup_is_without_waking_the_cabinet(tmp_path):
    async def check(client, admin, pi, calls):
        r = await client.get("/api/wizard-status")
        s = await r.json()
        assert r.status == 200 and s["done"] is False and s["step"] == "Name your jukebox" and s["finished"] == 0
        admin.wizard.state["finished_at"] = 1        # run again from the admin: the cabinet waits for the page
        assert admin.wizard.cabinet() == {"active": False}
        await submit(client, "finish")
        s = await (await client.get("/api/wizard-status")).json()
        assert s["done"] is True and s["summary"][0]["k"] == "Name"
        assert (await client.post("/api/wizard-again", json={}, headers=H)).status == 200
        assert (await (await client.get("/api/wizard-status")).json())["done"] is False
    run(tmp_path, check)


def test_the_cabinet_says_which_step_and_carries_its_yes_no(tmp_path):
    async def check(client, admin, pi, calls):
        await client.post("/api/wizard/lightcheck/show", json={"chain": "accent"}, headers=H)
        assert ("lights", "identify", "accent", str(setup_wizard.IDENTIFY_S)) in calls
        await view(client, "lightcheck")
        cab = admin.wizard.cabinet()
        assert cab["active"] and cab["step"] == "lightcheck" and cab["ask"]["q"].startswith("Is the upper panel")
        admin.wizard.heard_answer({"step": "lightcheck", "yes": True})
        v = await view(client, "lightcheck")
        assert v["step"]["cabinet_answer"]["yes"] is True and v["step"]["cabinet_answer"]["chain"] == "accent"
        await submit(client, "lightcheck")
        assert ("lights", "stop") in calls
    run(tmp_path, check)


def test_the_board_voltage_is_read_after_the_equals_sign():
    class Pi(setup_wizard.System):
        def __init__(self):
            pass

        async def run(self, *cmd, timeout=10, env=None):
            return "EXT5V_V volt(24)=5.09736000V\n"
    assert asyncio.run(Pi().volts()) == 5.1


def test_in_setup_mode_the_wifi_step_lists_networks_and_asks_to_join(tmp_path):
    async def check(client, admin, pi, calls):
        folder = tmp_path / "setupnet"
        write_atomic(setup_net.state_path(folder), json.dumps({
            "active": True, "ssid": "Juk3box Setup 1234", "password": "abcd2345", "url": "http://10.42.0.1:8080/setup",
            "networks": [{"ssid": "Home Wi-Fi", "signal": 80, "secure": True}]}))
        v = await view(client, "wifi")
        assert v["step"]["hotspot"] is True and v["step"]["networks"][0]["ssid"] == "Home Wi-Fi"
        r = await client.post("/api/wizard/wifi/join", json={"ssid": "Home Wi-Fi", "password": "short"}, headers=H)
        assert r.status == 400
        r = await client.post("/api/wizard/wifi/join", json={"ssid": "Home Wi-Fi", "password": "correct horse"}, headers=H)
        assert r.status == 200
        assert setup_net.take_requests(folder) == [{"action": "join", "ssid": "Home Wi-Fi", "password": "correct horse", "hidden": False, "id": ""}]
        cab = admin.wizard.cabinet()
        assert cab["active"] and cab["hotspot"]["ssid"] == "Juk3box Setup 1234" and cab["hotspot"]["qr"]
        assert cab["say"].startswith("Pick your Wi-Fi")
    run(tmp_path, check)


def test_the_cabinet_can_cancel_setup_mode_only_when_it_was_asked_for(tmp_path):
    async def check(client, admin, pi, calls):
        folder = tmp_path / "setupnet"
        for why, stops in (("not-set-up", []), ("asked", [{"action": "stop"}])):
            write_atomic(setup_net.state_path(folder), json.dumps({"active": True, "why": why, "ssid": "s", "password": "abcd2345"}))
            admin.wizard.heard_answer({"action": "stop_hotspot"})
            assert setup_net.take_requests(folder) == stops
    run(tmp_path, check)


KEY = "0123456789abcdef0123456789abcdef"


def test_the_lastfm_step_checks_the_key_then_saves_it_everywhere(tmp_path):
    async def check(client, admin, pi, calls):
        s = (await view(client, "lastfm"))["step"]
        assert s["set"] is False and s["skippable"] and s["get_key"].startswith("https://www.last.fm/api")
        out = await submit(client, "lastfm", {"key": "the shared secret?"}, status=400)
        assert "32 letters and numbers" in out["error"]
        pi.lastfm_answer = False
        out = await submit(client, "lastfm", {"key": KEY}, status=400)
        assert "turned that key down" in out["error"] and pi.lastfm is None
        pi.lastfm_answer = True
        out = await submit(client, "lastfm", {"key": " " + KEY.upper() + " "})
        assert out["step"]["id"] == "wholehome"
        assert ("lastfm", KEY) in calls and ("call", "jb-weathermix", "lastfm-key", KEY) in calls
        assert {"k": "Last.fm", "v": "On"} in admin.wizard.summary()
        # Back again: the saved key is kept without typing it.
        assert (await view(client, "lastfm"))["step"]["ends"] == "cdef"
        await submit(client, "lastfm", {})
        assert [c for c in calls if c[0] == "lastfm"] == [("lastfm", KEY)]
    run(tmp_path, check)


def test_lastfm_can_be_skipped_and_added_later_from_the_admin(tmp_path):
    async def check(client, admin, pi, calls):
        out = await submit(client, "lastfm", {"skip": True})
        assert out["step"]["id"] == "wholehome" and pi.lastfm is None
        assert (await (await client.get("/api/wizard-status")).json())["lastfm"]["set"] is False
        r = await client.put("/api/lastfm", json={"key": KEY}, headers=H)
        assert r.status == 200 and (await r.json())["ends"] == "cdef"
        r = await client.delete("/api/lastfm", headers=H)
        assert r.status == 200 and (await r.json())["set"] is False
        assert ("lastfm", "clear") in calls and ("call", "jb-weathermix", "lastfm-key", None) in calls
    run(tmp_path, check)


def test_a_new_jukebox_shows_no_address_until_it_can_be_reached(tmp_path):
    async def check(client, admin, pi, calls):
        w = admin.wizard
        w.online = False
        assert w.cabinet() == {"active": True, "waiting": True, "title": "Getting ready",
                               "say": "Looking for a network. Its own Wi-Fi comes up in about half a minute."}
        write_atomic(setup_net.state_path(w.setupnet_dir), json.dumps(
            {"active": True, "ssid": "Juk3box Setup 1234", "password": "abcd2345", "url": "http://juk3box.local:8080/setup"}))
        out = w.cabinet()
        assert "waiting" not in out and out["hotspot"]["ssid"] == "Juk3box Setup 1234"
        write_atomic(setup_net.state_path(w.setupnet_dir), json.dumps({"active": False}))
        w.online = True
        assert "waiting" not in w.cabinet() and w.cabinet()["address"].endswith(":8080/setup")
    run(tmp_path, check, password=False)


def test_the_cabinet_follows_a_wifi_join_and_the_step_completes_by_itself(tmp_path):
    async def check(client, admin, pi, calls):
        w = admin.wizard
        state = setup_net.state_path(w.setupnet_dir)
        base = {"active": True, "ssid": "Juk3box Setup 1234", "password": "abcd2345", "url": "http://juk3box.local:8080/setup"}
        write_atomic(state, json.dumps({**base, "join": {"state": "joining", "ssid": "Home Wi-Fi"}}))
        assert w.cabinet()["title"] == "Connecting to Home Wi-Fi" and w.cabinet()["waiting"]
        write_atomic(state, json.dumps({**base, "join": {"state": "failed", "ssid": "Home Wi-Fi",
                                                          "reason": "The password didn't work."}}))
        assert w.cabinet()["problem"] == "Couldn't join Home Wi-Fi. The password didn't work."
        write_atomic(state, json.dumps({"active": False, "join": {"state": "joined", "ssid": "Home Wi-Fi"}}))
        await w.joined_wifi()
        assert "wifi" in w.state["completed"]
        out = w.cabinet()           # no phone on the page: the cabinet still says where to carry on
        assert out["active"] and not out.get("waiting") and out["address"].endswith(":8080/setup")
    run(tmp_path, check)


def test_the_controls_step_teaches_the_buttons_and_saves_them(tmp_path):
    async def check(client, admin, pi, calls):
        v = await view(client, "controls")
        assert v["step"]["phase"] == "format" and v["step"]["format"]["count"] == 100
        r = await client.post("/api/wizard/controls/preview", json={"spec": {"kind": "letters", "letters": "", "digits": "1"}}, headers=H)
        assert r.status == 400
        r = await client.post("/api/wizard/controls/preview", json={"spec": {"kind": "letters", "letters": "AB", "digits": "12"}}, headers=H)
        assert (await r.json())["describe"] == "4 songs a page, A1 to B2"
        r = await client.post("/api/wizard/controls/start", headers=H,
                              json={"spec": {"kind": "letters", "letters": "AB", "digits": "12"}, "knob": False})
        assert (await r.json())["prompt"] == "Press Cancel"
        assert ("call", "jukebox/jb-input/cmd", {"action": "learn", "on": True}) in calls
        assert admin.wizard.cabinet()["controls"]["prompt"] == "Press Cancel"
        t = 1000
        for code in ("KEY_ESC", "KEY_A", "KEY_B", "KEY_1", "KEY_2"):
            for state in ("down", "up"):
                t += 300
                admin.wizard.heard_input("jukebox/jb-input/learn", {"device": "239a:80f4:Jukebox JB Keypad",
                                                                    "role": "keypad", "code": code, "state": state, "t": t})
        v = await view(client, "controls")
        assert v["step"]["phase"] == "try"
        v = await submit(client, "controls")
        assert v["step"]["id"] != "controls"
        saved = json.loads((tmp_path / "controls.json").read_text())
        assert saved["keymap"]["KEY_ESC"] == "reset" and saved["keypads"] == ["239a:80f4:Jukebox JB Keypad"]
        assert json.loads((tmp_path / "settings.json").read_text())["selection"] == {"kind": "letters", "letters": "AB", "digits": "12"}
        assert ("call", "jukebox/jb-input/cmd", {"action": "learn", "on": False}) in calls
    run(tmp_path, check)


def test_no_buttons_saves_only_the_format(tmp_path):
    async def check(client, admin, pi, calls):
        await submit(client, "controls", {"no_buttons": True, "spec": selection.PRESETS["rockola"]})
        assert json.loads((tmp_path / "settings.json").read_text())["selection"]["kind"] == "numbers"
        assert not (tmp_path / "controls.json").exists()
    run(tmp_path, check)


def test_the_one_name_gives_the_address():
    from services.jb_admin.setup_wizard import network_name
    assert network_name("Garage Jukebox") == "garage-jukebox"
    assert network_name("  Alex's Café — Jukebox! ") == "alex-s-cafe-jukebox"
    assert network_name("🎵") == "juk3box"
    assert len(network_name("x" * 80)) == 63


def test_the_address_follows_the_name_at_once(tmp_path):
    async def check(client, admin, pi, calls):
        out = await submit(client, "name", {"name": "Den Jukebox"})
        assert ("call", "Den Jukebox") in calls                              # renamed with its address
        moved = out["moved"]
        assert moved["address"] == f"{socket.gethostname()}.local" and moved["pass"]
        # The pass signs a browser in at the new address, once.
        client.session.cookie_jar.clear()
        assert (await client.get("/api/wizard")).status == 401
        assert (await client.post("/api/handoff", json={"pass": moved["pass"]}, headers=H)).status == 200
        assert (await client.get("/api/wizard")).status == 200
        client.session.cookie_jar.clear()
        assert (await client.post("/api/handoff", json={"pass": moved["pass"]}, headers=H)).status == 401
        assert (await client.post("/api/handoff", json={"pass": "made-up"}, headers=H)).status == 401
    run(tmp_path, check)


def test_renaming_from_the_admin_moves_everything(tmp_path):
    async def check(client, admin, pi, calls):
        async def rename(name, address=True):
            calls.append(("rename", name, address))
            return "garage-jukebox.local"
        admin.rename_jukebox = rename
        r = await client.put("/api/jukebox-name", json={"name": " Garage Jukebox "}, headers=H)
        assert r.status == 200 and await r.json() == {"name": "Garage Jukebox", "address": "garage-jukebox.local"}
        assert ("rename", "Garage Jukebox", True) in calls
        assert admin.wizard.answer("name")["name"] == "Garage Jukebox"
        assert (await client.put("/api/jukebox-name", json={"name": ""}, headers=H)).status == 400
        assert set(await (await client.get("/api/jukebox-name")).json()) == {"name", "address"}
    run(tmp_path, check)
