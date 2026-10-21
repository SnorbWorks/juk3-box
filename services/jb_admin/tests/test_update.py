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
