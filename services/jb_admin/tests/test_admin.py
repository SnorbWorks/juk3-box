import asyncio
import io
import json
import logging
import shutil
import zipfile
from pathlib import Path

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from services.jb_admin import main, registry
from services.jb_admin.auth import Auth

SHIPPED = Path(__file__).resolve().parents[3] / "themes"
H = {"X-JB-Admin": "1"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def config(tmp_path):
    return {**main.DEFAULTS,
            "theme_dirs": [str(SHIPPED), str(tmp_path / "themes")],
            "settings_path": str(tmp_path / "settings.json"),
            "owner_layouts_dir": str(tmp_path / "layouts"),
            "drafts_dir": str(tmp_path / "drafts"),
            "media_dir": str(tmp_path / "media"),
            "media_cache_dir": str(tmp_path / "cache"),
            "admin_dir": str(tmp_path / "admin"),
            "module_dirs": [str(Path(__file__).resolve().parents[2])]}


def run(tmp_path, check, login=True):
    async def go():
        app = main.build_app(config(tmp_path), logging.getLogger("test"), bus_enabled=False)
        async with TestClient(TestServer(app)) as client:
            if login:
                r = await client.post("/api/setup", json={"password": "correct horse"}, headers=H)
                assert r.status == 200
            await check(client, app[main.ADMIN_KEY])
    asyncio.run(go())


def theme_zip(theme_id="mine", top="mine/", extra=None, manifest_changes=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in (SHIPPED / "bare").rglob("*"):
            if p.is_file():
                rel = p.relative_to(SHIPPED / "bare").as_posix()
                data = p.read_bytes()
                if rel == "theme.json":
                    m = json.loads(data)
                    m["id"] = theme_id
                    m.update(manifest_changes or {})
                    data = json.dumps(m).encode()
                z.writestr(top + rel, data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def upload(data, filename="t.zip"):
    form = FormData()
    form.add_field("file", data, filename=filename, content_type="application/octet-stream")
    return form


# Login

def test_setup_then_login_logout(tmp_path):
    async def check(c, admin):
        assert (await (await c.get("/api/session")).json()) == {"setup_needed": True, "logged_in": False}
        assert (await c.get("/api/settings")).status == 401
        r = await c.post("/api/setup", json={"password": "short"}, headers=H)
        assert r.status == 400
        r = await c.post("/api/setup", json={"password": "correct horse"}, headers=H)
        assert r.status == 200
        assert (await (await c.get("/api/session")).json())["logged_in"]
        assert (await c.post("/api/setup", json={"password": "another one"}, headers=H)).status == 403
        await c.post("/api/logout", headers=H)
        assert (await c.get("/api/settings")).status == 401
        assert (await c.post("/api/login", json={"password": "wrong wrong"}, headers=H)).status == 401
        assert (await c.post("/api/login", json={"password": "correct horse"}, headers=H)).status == 200
        assert (await c.get("/api/settings")).status == 200
    run(tmp_path, check, login=False)


def test_changes_need_the_admin_header(tmp_path):
    async def check(c, admin):
        r = await c.put("/api/settings", json={"values": {"songs_per_page": 50}})
        assert r.status == 403
    run(tmp_path, check)


def test_pages_redirect_to_login_and_preview_is_private(tmp_path):
    async def check(c, admin):
        await c.post("/api/logout", headers=H)
        r = await c.get("/preview", allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/"
        assert (await c.get("/engine/js/main.js", allow_redirects=False)).status == 302
        assert (await c.get("/")).status == 200
    run(tmp_path, check)


def test_lockout_after_repeated_failures(tmp_path):
    t = [1000.0]
    auth = Auth(tmp_path, clock=lambda: t[0])
    for _ in range(5):
        auth.failed("1.2.3.4")
    assert auth.locked_for("1.2.3.4") > 0
    assert auth.locked_for("5.6.7.8") == 0
    t[0] += 61
    assert auth.locked_for("1.2.3.4") == 0


def test_sessions_file_holds_no_tokens(tmp_path):
    auth = Auth(tmp_path)
    token = auth.open_session()
    assert token not in (tmp_path / "sessions.json").read_text()
    assert auth.valid(token) and not auth.valid("nope")
    auth.close_session(token)
    assert not auth.valid(token)


# Settings

def test_settings_validate_and_keep_unknown_keys(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"theme": "bare", "future": 1}))

    async def check(c, admin):
        data = await (await c.get("/api/settings")).json()
        assert data["values"]["songs_per_page"] == 100 and "nav" in data["schema"]
        r = await c.put("/api/settings", json={"values": {"songs_per_page": 55}}, headers=H)
        assert r.status == 400 and "songs_per_page" in (await r.json())["problems"]
        r = await c.put("/api/settings", json={"values": {"nav": ["home", "home"]}}, headers=H)
        assert r.status == 400
        r = await c.put("/api/settings", json={"values": {"songs_per_page": 50, "nav": ["home", "all-songs"],
                                                          "idle_home_minutes": 0}}, headers=H)
        assert r.status == 200
        saved = json.loads((tmp_path / "settings.json").read_text())
        assert saved == {"theme": "bare", "future": 1, "songs_per_page": 50, "nav": ["home", "all-songs"],
                         "idle_home_minutes": 0}
    run(tmp_path, check)


# Themes

def test_theme_install_activate_delete(tmp_path):
    async def check(c, admin):
        data = await (await c.get("/api/themes")).json()
        ids = {t["id"]: t for t in data["themes"]}
        assert ids["bare"]["shipped"] and ids["neon"]["shipped"]

        z = theme_zip(extra={"mine/evil.js": "alert(1)", "mine/run.sh": "rm -rf /"})
        r = await c.post("/api/themes/upload", data=upload(z), headers=H)
        info = await r.json()
        assert r.status == 200, info
        assert info["id"] == "mine" and set(info["dropped"]) == {"evil.js", "run.sh"}
        installed = tmp_path / "themes" / "mine"
        assert (installed / "theme.json").is_file() and not (installed / "evil.js").exists()
        assert not any(p.name.startswith(".incoming") for p in (tmp_path / "themes").iterdir())

        # Same id again: refused unless replacing.
        assert (await c.post("/api/themes/upload", data=upload(z), headers=H)).status == 400
        r = await c.post("/api/themes/upload?replace=1", data=upload(theme_zip(manifest_changes={"version": "2.0.0"})), headers=H)
        assert r.status == 200 and (await r.json())["version"] == "2.0.0"

        assert (await c.post("/api/themes/activate", json={"id": "mine"}, headers=H)).status == 200
        assert json.loads((tmp_path / "settings.json").read_text())["theme"] == "mine"
        assert (await c.delete("/api/themes/mine", headers=H)).status == 400        # active
        assert (await c.delete("/api/themes/bare", headers=H)).status == 400        # shipped
        await c.post("/api/themes/activate", json={"id": "bare"}, headers=H)
        assert (await c.delete("/api/themes/mine", headers=H)).status == 200
        assert not installed.exists()
    run(tmp_path, check)


def test_dangerous_or_broken_zips_are_refused_and_leave_nothing(tmp_path):
    async def check(c, admin):
        bad = [
            theme_zip(extra={"../escape.css": "x"}, top=""),
            theme_zip(theme_id="bare"),                                    # a shipped id
            theme_zip(manifest_changes={"styles": ["missing.css"]}),       # fails validation
            b"not a zip",
        ]
        for z in bad:
            r = await c.post("/api/themes/upload", data=upload(z), headers=H)
            assert r.status == 400, await r.text()
        assert not (tmp_path / "escape.css").exists()
        assert not (tmp_path / "themes").exists() or not any((tmp_path / "themes").iterdir())
    run(tmp_path, check)


def test_activating_a_broken_theme_is_refused(tmp_path):
    async def check(c, admin):
        r = await c.post("/api/themes/activate", json={"id": "nope"}, headers=H)
        assert r.status == 409
    run(tmp_path, check)


# Layouts

HOME = {"class": "home", "children": [{"block": "heading", "text": "Hi"}, {"block": "section", "type": "recents"}]}


def test_draft_publish_reset(tmp_path):
    async def check(c, admin):
        lst = await (await c.get("/api/layouts/bare")).json()
        assert {l["name"] for l in lst["layouts"]} >= {"frame", "home", "all-songs"}

        r = await c.put("/api/layouts/bare/home/draft", json={"tree": {"children": [{"block": "screen"}]}}, headers=H)
        assert r.status == 400                                             # screen outside the frame
        r = await c.put("/api/layouts/bare/home/draft", json={"tree": HOME}, headers=H)
        assert r.status == 200

        got = await (await c.get("/api/layouts/bare/home")).json()
        assert got["draft"] == HOME and got["published"] is None and got["theme"]["children"]

        # The preview shows the draft; the kiosk (published only) doesn't yet.
        prev = await (await c.get("/api/theme?preview=1&theme=bare")).json()
        assert prev["layouts"]["home"] == HOME and prev["layout_origins"]["home"] == "draft"
        assert not (tmp_path / "layouts" / "bare" / "home.json").exists()

        assert (await c.post("/api/layouts/bare/home/publish", headers=H)).status == 200
        assert json.loads((tmp_path / "layouts" / "bare" / "home.json").read_text()) == HOME
        assert (await c.post("/api/layouts/bare/home/publish", headers=H)).status == 400   # nothing new

        assert (await c.delete("/api/layouts/bare/home", headers=H)).status == 200
        assert not (tmp_path / "layouts" / "bare" / "home.json").exists()
    run(tmp_path, check)


def test_layout_names_cannot_escape(tmp_path):
    async def check(c, admin):
        r = await c.put("/api/layouts/bare/..%2Fx/draft", json={"tree": HOME}, headers=H)
        assert r.status in (400, 404)
        r = await c.put("/api/layouts/..%2Fetc/home/draft", json={"tree": HOME}, headers=H)
        assert r.status in (404, 409)
    run(tmp_path, check)


# Media

def test_media_upload_checks_the_bytes(tmp_path):
    async def check(c, admin):
        r = await c.post("/api/media", data=upload(PNG, "My Photo!.png"), headers=H)
        assert r.status == 200
        name = (await r.json())["name"]
        assert name == "My-Photo.png"
        r = await c.post("/api/media", data=upload(PNG, "My Photo!.png"), headers=H)
        assert (await r.json())["name"] == "My-Photo-2.png"
        r = await c.post("/api/media", data=upload(b"<svg onload=x>", "x.png"), headers=H)
        assert r.status == 400
        lst = await (await c.get("/api/media")).json()
        assert len(lst["media"]) == 2
        assert (await c.get(f"/media/{name}")).status == 200
        assert (await c.delete(f"/api/media/{name}", headers=H)).status == 200
    run(tmp_path, check)


# Preview

def test_preview_is_always_in_preview_mode_with_the_bus_on_the_network(tmp_path):
    async def check(c, admin):
        r = await c.get("/preview", allow_redirects=False)
        assert r.status == 302 and "preview=1" in r.headers["Location"]
        r = await c.get("/preview?preview=1")
        assert r.status == 200 and "jb-root" in await r.text()
        csp = r.headers["Content-Security-Policy"]
        assert "ws://127.0.0.1:9001" in csp and "frame-ancestors 'self'" in csp
        admin_csp = (await c.get("/")).headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in admin_csp
    run(tmp_path, check)


# Registry

def test_registry_views_manifests_and_liveness(tmp_path):
    t = [100.0]
    reg = registry.Registry([Path(__file__).resolve().parents[2]], clock=lambda: t[0])
    reg.heard("jukebox/jb-core/heartbeat", {"service": "jb-core"})
    reg.heard("jukebox/analysis/audio/status", {"online": False, "module": "jb-analyzer"})
    reg.heard("jukebox/jb-stranger/heartbeat", {})
    view = {m["id"]: m for m in reg.view({"jb-lights": "active"})}
    assert view["jb-core"]["up"] is True and "service" in view["jb-core"]["roles"]
    assert view["jb-analyzer"]["up"] is False
    assert view["jb-lights"]["up"] is True and view["jb-lights"]["up_from"] == "systemd"
    assert view["jb-stranger"]["undeclared"]
    assert view["jb-admin"]["roles"] == ["service"]
    t[0] += 30
    assert {m["id"]: m for m in reg.view()}["jb-core"]["up"] is False
