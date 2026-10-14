import asyncio
import io
import json
import logging
import shutil
import zipfile
from pathlib import Path

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from services.jb_admin import main, module_mgr
from services.jb_admin.tests.test_admin import H, config
from services.jb_ui import module_blocks

REPO = Path(__file__).resolve().parents[3]
WEATHER = REPO / "modules" / "jb-weather"


def module_zip(changes=None, extra=None, top="jb-weather"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in WEATHER.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                rel = p.relative_to(WEATHER).as_posix()
                data = p.read_bytes()
                if rel == "module.json" and changes:
                    m = json.loads(data)
                    m.update(changes)
                    data = json.dumps(m).encode()
                z.writestr(f"{top}/{rel}", data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def form(data):
    f = FormData()
    f.add_field("file", data, filename="m.zip", content_type="application/zip")
    return f


def run(tmp_path, check, healthy=True):
    """The admin with a pretend jb-modctl: requests are answered in-process,
    and an "installed" module heartbeats if `healthy`."""
    async def go():
        cfg = {**config(tmp_path), "module_dirs": [str(REPO / "services"), str(tmp_path / "modules")],
               "module_settings_dir": str(tmp_path / "module-settings"), "modctl_dir": str(tmp_path / "modctl")}
        (tmp_path / "modctl" / "requests").mkdir(parents=True)
        app = main.build_app(cfg, logging.getLogger("test"), bus_enabled=False)
        admin = app[main.ADMIN_KEY]
        calls = []

        async def ctl(verb, *args, timeout=90):
            calls.append((verb, *args))
            if verb == "install" and healthy:
                async def beat():
                    for _ in range(12):
                        admin.registry.heard(f"jukebox/{args[0]}/heartbeat", {})
                        await asyncio.sleep(0.5)
                asyncio.get_running_loop().create_task(beat())
        admin.modules.ctl = ctl
        async with TestClient(TestServer(app)) as client:
            assert (await client.post("/api/setup", json={"password": "correct horse"}, headers=H)).status == 200
            await check(client, admin, calls)
    asyncio.run(go())


def test_weather_is_a_valid_installable_module():
    m = json.loads((WEATHER / "module.json").read_text())
    assert module_mgr.check_manifest(m, WEATHER, set()) == []
    blocks, problems = module_blocks.module_blocks([WEATHER.parent])
    assert problems == [] and "weather" in blocks
    assert blocks["weather"]["icons"]["sun"]["svg"].startswith("<svg")
    assert "<script" not in blocks["weather"]["template"]


def test_installed_modules_are_held_to_their_own_namespace():
    m = json.loads((WEATHER / "module.json").read_text())
    bad = {**m, "id": "jb-core", "publishes": ["jukebox/jb-audio/cmd"], "permissions": {"network": "lan", "devices": ["gpio"]},
           "resources": {"cpu": 400}, "contract": 9}
    problems = " | ".join(module_mgr.check_manifest(bad, WEATHER, {"jb-core"}))
    for words in ("contract version 9", "jukebox's own modules", "only under jukebox/jb-core/", "local network", "devices", "1 to 100"):
        assert words in problems, words


def test_module_styles_stay_in_their_scope(tmp_path):
    assert module_blocks.contained(".a { color: red } @media (x) { .b { c: d } }")
    assert not module_blocks.contained(".a { } } body { display: none } .x {")
    assert not module_blocks.contained('@import "x.css"; .a {}')
    assert module_blocks.contained('.a::after { content: "}" }')
    shutil.copytree(WEATHER, tmp_path / "jb-weather")
    body, kind = module_blocks.asset([tmp_path], "jb-weather", "blocks/weather.css")
    assert kind == "text/css" and body.startswith(b"@scope (jb-weather) {")
    (tmp_path / "jb-weather" / "blocks" / "weather.css").write_text("} body { display: none }")
    assert module_blocks.asset([tmp_path], "jb-weather", "blocks/weather.css") is None
    assert module_blocks.asset([tmp_path], "jb-weather", "main.py") is None       # code is never served
    assert module_blocks.asset([tmp_path], "jb-weather", "../jb-weather/module.json") is None


def test_upload_review_install_settings_remove(tmp_path):
    async def check(c, admin, calls):
        r = await c.post("/api/modules/upload", data=form(module_zip()), headers=H)
        staged = await r.json()
        assert r.status == 200 and staged["id"] == "jb-weather" and staged["update_from"] is None
        assert any("internet" in x["text"] for x in staged["review"])
        assert not (tmp_path / "modules" / "jb-weather").exists()                   # nothing until approved
        r = await c.post("/api/modules/install", json={"token": staged["token"]}, headers=H)
        assert r.status == 200, await r.text()
        assert calls == [("install", "jb-weather")]
        assert (tmp_path / "modules" / "jb-weather" / "module.json").is_file()
        info = await (await c.get("/api/modules/jb-weather")).json()
        assert info["installed"] and info["unit"] == "jbmod-jb-weather"
        assert info["settings"]["values"]["place"] == "New York"
        r = await c.put("/api/modules/jb-weather/settings", json={"values": {"place": "Austin", "units": "celsius"}}, headers=H)
        assert r.status == 200 and (await r.json())["values"]["place"] == "Austin"
        r = await c.put("/api/modules/jb-weather/settings", json={"values": {"refresh": 1}}, headers=H)
        assert r.status == 400                                                        # under its minimum
        blocks = await (await c.get("/api/blocks")).json()
        assert blocks["blocks"]["weather"]["module"] == "jb-weather"
        assert (await c.post("/api/modules/jb-weather/restart", headers=H)).status == 200
        assert calls[-1] == ("unit", "restart", "jbmod-jb-weather")
        assert (await c.delete("/api/modules/jb-core", headers=H)).status == 400       # built in
        assert (await c.delete("/api/modules/jb-weather", headers=H)).status == 200
        assert calls[-1] == ("remove", "jb-weather")
        assert not (tmp_path / "modules" / "jb-weather").exists()
        assert not (tmp_path / "module-settings" / "jb-weather.json").exists()
    run(tmp_path, check)


def test_a_module_that_never_starts_is_removed(tmp_path):
    async def check(c, admin, calls):
        staged = await (await c.post("/api/modules/upload", data=form(module_zip()), headers=H)).json()
        admin.modules.health_s = 2
        r = await c.post("/api/modules/install", json={"token": staged["token"]}, headers=H)
        assert r.status == 400 and "removed" in (await r.json())["error"]
        assert calls[-1] == ("remove", "jb-weather")
        assert not (tmp_path / "modules" / "jb-weather").exists()
    run(tmp_path, check, healthy=False)


def test_dangerous_module_zips_are_refused(tmp_path):
    async def check(c, admin, calls):
        for data in (module_zip(extra={"jb-weather/evil.sh": "rm -rf /"}),
                     module_zip(extra={"../outside.py": "x"}),
                     module_zip(changes={"id": "jb-core"}),
                     b"not a zip"):
            r = await c.post("/api/modules/upload", data=form(data), headers=H)
            assert r.status == 400, await r.text()
        assert not list((tmp_path / "modules" / ".staging").glob("*")) if (tmp_path / "modules" / ".staging").exists() else True
        assert calls == []
    run(tmp_path, check)
