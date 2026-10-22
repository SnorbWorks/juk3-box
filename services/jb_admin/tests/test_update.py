"""deploy/jb-update's own parts (signatures with the real openssl), and the
admin's Updates routes."""

import asyncio
import importlib.machinery
import importlib.util
import io
import json
import logging
import subprocess
import tarfile
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from services.jb_admin import main
from services.jb_admin.tests.test_admin import H, config

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "jb-update"
loader = importlib.machinery.SourceFileLoader("jb_update", str(SCRIPT))
spec = importlib.util.spec_from_loader("jb_update", loader)
up = importlib.util.module_from_spec(spec)
loader.exec_module(up)


def test_only_a_higher_version_is_newer():
    assert up.newer("v1.2.10", "v1.2.9")
    assert up.newer("v2.0.0", "v1.9.9")
    assert not up.newer("v1.2.3", "v1.2.3")
    assert not up.newer("v1.2.2", "v1.2.3")
    assert not up.newer("v1.3.0", None)            # a development copy never updates
    assert not up.newer("nonsense", "v1.0.0")


def test_a_copy_without_a_version_is_a_developers(tmp_path):
    assert up.current_version(tmp_path) is None
    (tmp_path / "VERSION").write_text("v0.3.1\n")
    assert up.current_version(tmp_path) == "v0.3.1"
    (tmp_path / "VERSION").write_text("main\n")
    assert up.current_version(tmp_path) is None


def keypair(tmp_path, name):
    key, pub = tmp_path / f"{name}.pem", tmp_path / f"{name}.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub)], check=True, capture_output=True)
    return key, pub


def sign(key, path):
    sig = path.with_suffix(".sig")
    subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(path), "-out", str(sig)],
                   check=True, capture_output=True)
    return sig


def bundle(tmp_path, files):
    path = tmp_path / "code.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return path


def test_only_a_bundle_signed_with_this_jukeboxs_key_is_taken(tmp_path):
    ours, ours_pub = keypair(tmp_path, "ours")
    theirs, _ = keypair(tmp_path, "theirs")
    b = bundle(tmp_path, {"VERSION": "v1.0.1\n"})
    sha = up.hashlib.sha256(b.read_bytes()).hexdigest()
    up.verify(b, sign(ours, b), sha, ours_pub)
    with pytest.raises(up.Failed, match="signed"):
        up.verify(b, sign(theirs, b), sha, ours_pub)
    with pytest.raises(up.Failed, match="checksum"):
        up.verify(b, sign(ours, b), "0" * 64, ours_pub)


def test_unpacking_keeps_everything_inside(tmp_path):
    good = bundle(tmp_path, {"VERSION": "v1.0.1\n", "services/x.py": "print(1)\n"})
    up.unpack(good, tmp_path / "new")
    assert (tmp_path / "new" / "services" / "x.py").is_file()
    bad = bundle(tmp_path, {"../escape.txt": "no"})
    with pytest.raises(Exception):
        up.unpack(bad, tmp_path / "new2")
    assert not (tmp_path / "escape.txt").exists()


def test_swapping_in_and_back_moves_the_python_packages(tmp_path, monkeypatch):
    repo, prev, new = tmp_path / "juk3-box", tmp_path / "juk3-box.prev", tmp_path / "juk3-box.new"
    monkeypatch.setattr(up, "REPO", repo)
    monkeypatch.setattr(up, "PREV", prev)
    (repo / "venv").mkdir(parents=True)
    (repo / "VERSION").write_text("v1.0.0\n")
    new.mkdir()
    (new / "VERSION").write_text("v1.0.1\n")
    up.swap_in(new)
    assert up.current_version(repo) == "v1.0.1" and (repo / "venv").is_dir() and not (prev / "venv").exists()
    up.swap_back()
    assert up.current_version(repo) == "v1.0.0" and (repo / "venv").is_dir() and not prev.exists()


def test_the_admin_shows_updates_and_asks_the_helper(tmp_path):
    async def go():
        folder = tmp_path / "update"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps({"current": "v1.0.0", "latest": "v1.1.0", "available": True,
                                                       "checked_at": 1, "release": {"code": "x"}}))
        app = main.build_app({**config(tmp_path), "update_dir": str(folder)}, logging.getLogger("test"), bus_enabled=False)
        admin = app[main.ADMIN_KEY]
        asked = []

        async def ctl(verb, *args, timeout=60):
            asked.append((verb, *args))
            if args == ("auto-off",):
                (folder / "settings.json").write_text('{"auto": false}')
            return "ok"
        admin.setup_ctl = ctl
        async with TestClient(TestServer(app)) as c:
            assert (await c.post("/api/setup", json={"password": "correct horse"}, headers=H)).status == 200
            u = await (await c.get("/api/update")).json()
            assert u["available"] and u["latest"] == "v1.1.0" and u["auto"] is True and "release" not in u
            assert (await c.post("/api/update/apply", json={}, headers=H)).status == 200
            r = await c.put("/api/update/auto", json={"on": False}, headers=H)
            assert (await r.json())["auto"] is False
            assert (await c.post("/api/update/reboot", json={}, headers=H)).status == 404
        assert asked == [("update", "apply"), ("update", "auto-off")]
    asyncio.run(go())


# The Updates tab: Raspberry Pi OS and everything in one place -----------------

APT_LIST = """Listing...
libssl3t64/stable-security 3.5.4-1~deb13u2 arm64 [upgradable from: 3.5.4-1~deb13u1]
chromium/stable 142.0.1-1~deb13u1+rpt1 arm64 [upgradable from: 141.0.2-1~deb13u1+rpt1]
openssl/stable-security,stable 3.5.4-1~deb13u2 arm64 [upgradable from: 3.5.4-1~deb13u1]
"""


def test_security_fixes_are_told_apart():
    security, other = up.parse_upgradable(APT_LIST)
    assert [p["name"] for p in security] == ["libssl3t64", "openssl"]
    assert other == [{"name": "chromium", "from": "141.0.2-1~deb13u1+rpt1", "to": "142.0.1-1~deb13u1+rpt1"}]


def test_whats_inside_a_release_comes_from_its_files():
    from services.jb_admin import updates
    inside = {p["name"]: p for p in updates.included()}
    assert inside["aiohttp"]["kind"] == "Python package" and inside["aiohttp"]["version"]
    assert "aiohappyeyeballs" not in inside                    # only what requirements.txt names
    assert inside["MQTT.js"]["version"].count(".") == 2 and "CircuitPython" in inside


def test_the_updates_tab_and_the_dashboards_line(tmp_path, monkeypatch):
    from services.jb_admin import updates
    monkeypatch.setattr(updates, "boot_time", lambda: 1000.0)
    monkeypatch.setattr(updates, "system_versions", lambda: [{"name": "Chromium", "does": "x", "version": "142"}])

    async def go():
        folder = tmp_path / "update"
        folder.mkdir()
        state = {"current": "v1.0.0", "latest": "v1.0.0", "available": False, "checked_at": 1,
                 "os": {"checked_at": 2, "security": [{"name": "openssl", "from": "1", "to": "2"}], "other": [],
                        "restart_needed_at": 500.0}}
        (folder / "state.json").write_text(json.dumps(state))
        app = main.build_app({**config(tmp_path), "update_dir": str(folder)}, logging.getLogger("test"), bus_enabled=False)
        admin = app[main.ADMIN_KEY]
        asked = []

        async def ctl(verb, *args, timeout=60):
            asked.append((verb, *args))
            return "started"
        admin.setup_ctl = ctl
        async with TestClient(TestServer(app)) as c:
            assert (await c.post("/api/setup", json={"password": "correct horse"}, headers=H)).status == 200
            u = await (await c.get("/api/updates")).json()
            assert u["os"]["restart_needed"] is False                 # asked before this start: done
            assert u["announce"] == {"kind": "available", "text": "Ready to install: 1 security fix."}
            assert u["system"][0]["name"] == "Chromium" and u["included"]
            state["os"]["restart_needed_at"] = 2000.0
            (folder / "state.json").write_text(json.dumps(state))
            u = await (await c.get("/api/updates")).json()
            assert u["os"]["restart_needed"] is True and u["announce"]["kind"] == "restart"
            for what in ("check", "security", "all"):
                assert (await c.post(f"/api/updates/os/{what}", json={}, headers=H)).status == 200
            assert (await c.post("/api/updates/os/everything", json={}, headers=H)).status == 404
        assert asked == [("update", "os-check"), ("update", "os-security"), ("update", "os-all")]
    asyncio.run(go())


def test_the_overnight_restart_waits_for_quiet(tmp_path):
    from services.jb_admin import updates

    class Admin:
        live = {"now_playing_helper": {"status": "playing"}}
    u = updates.Updates(Admin())
    assert not u.quiet()
    Admin.live = {"now_playing_helper": {"status": "paused"}, "now_playing": {"status": "stopped"}}
    assert u.quiet()


def test_the_install_queue_follows_apt():
    p = up.Progress(["libssl3t64", "openssl", "chromium"])
    states = lambda: [q["state"] for q in p.view()]
    assert states() == ["waiting"] * 3
    assert p.line("dlstatus:1:12.5:Retrieving file 1 of 3\n") and p.download == 10
    assert not p.line("dlstatus:1:13.0:Retrieving file 1 of 3\n")            # same 5% step
    assert p.line("pmstatus:libssl3t64:arm64:10:Preparing libssl3t64 (arm64)\n")
    assert states() == ["running", "waiting", "waiting"]
    p.line("pmstatus:openssl:20:Unpacking openssl (arm64)\n")              # apt unpacks ahead
    assert states() == ["waiting", "running", "waiting"]
    p.line("pmstatus:libssl3t64:arm64:40:Configuring libssl3t64 (arm64)\n")  # back to set it up
    p.line("pmstatus:libssl3t64:arm64:45:Installed libssl3t64 (arm64)\n")
    assert states() == ["done", "waiting", "waiting"]
    p.line("pmstatus:openssl:60:Configuring openssl (arm64)\n")
    p.line("pmstatus:openssl:62:Installed openssl (arm64)\n")
    p.line("pmstatus:dpkg-exec:70:Running dpkg\n")                         # not one of ours
    p.line("pmstatus:chromium:80:Installing chromium (arm64)\n")
    assert states() == ["done", "done", "running"]
    p.line("pmstatus:chromium:85:Installed chromium (arm64): 155.0\n")       # a colon in the message
    assert states() == ["done"] * 3
    assert not p.line("garbage\n") and not p.line("pmstatus:chromium:x:y\n")
