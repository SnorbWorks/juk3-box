import asyncio
import json
import logging

from aiohttp.test_utils import TestClient, TestServer

from services.jb_admin import main, setup_wizard
from shared import setup_net
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
        return {"via": "wifi", "name": "Ducks Home"}

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

        async def ctl(verb, *args):
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
        assert v["step"]["id"] == "wifi" and v["step"]["name"] == "Ducks Home"
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
        assert out == {"done": True}
        assert json.loads((tmp_path / "setup.json").read_text())["done"] is True
        assert admin.wizard.cabinet() == {"active": False}
    run(tmp_path, check)


def test_the_admin_sees_where_setup_is_without_waking_the_cabinet(tmp_path):
    async def check(client, admin, pi, calls):
        r = await client.get("/api/wizard-status")
        s = await r.json()
        assert r.status == 200 and s["done"] is False and s["step"] == "Name your jukebox" and s["finished"] == 0
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
            "networks": [{"ssid": "Ducks Home", "signal": 80, "secure": True}]}))
        v = await view(client, "wifi")
        assert v["step"]["hotspot"] is True and v["step"]["networks"][0]["ssid"] == "Ducks Home"
        r = await client.post("/api/wizard/wifi/join", json={"ssid": "Ducks Home", "password": "short"}, headers=H)
        assert r.status == 400
        r = await client.post("/api/wizard/wifi/join", json={"ssid": "Ducks Home", "password": "correct horse"}, headers=H)
        assert r.status == 200
        assert setup_net.take_requests(folder) == [{"action": "join", "ssid": "Ducks Home", "password": "correct horse", "hidden": False, "id": ""}]
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
