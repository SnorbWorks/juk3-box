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
            "revisions_dir": str(tmp_path / "revisions"),
            "owner_styles_dir": str(tmp_path / "styles"),
            "style_drafts_dir": str(tmp_path / "style-drafts"),
            "style_revisions_dir": str(tmp_path / "style-revisions"),
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

def picture(fmt="PNG", size=(40, 30), exif_rotate=None):
    from PIL import Image
    buf = io.BytesIO()
    im = Image.new("RGB", size, (200, 40, 40))
    if exif_rotate:
        exif = Image.Exif()
        exif[0x0112] = exif_rotate
        im.save(buf, fmt, exif=exif)
    else:
        im.save(buf, fmt)
    return buf.getvalue()


def test_media_upload_checks_the_bytes(tmp_path):
    async def check(c, admin):
        r = await c.post("/api/media", data=upload(picture(), "My Photo!.png"), headers=H)
        assert r.status == 200
        name = (await r.json())["name"]
        assert name == "My-Photo.png"
        r = await c.post("/api/media", data=upload(picture(), "My Photo!.png"), headers=H)
        assert (await r.json())["name"] == "My-Photo-2.png"
        r = await c.post("/api/media", data=upload(b"<svg onload=x>", "x.png"), headers=H)
        assert r.status == 400
        r = await c.post("/api/media", data=upload(PNG, "broken.png"), headers=H)    # a PNG header, no picture
        assert r.status == 400
        lst = await (await c.get("/api/media")).json()
        assert len(lst["media"]) == 2 and lst["media"][0]["kind"] == "image"
        assert not [p for p in (tmp_path / "media").iterdir() if p.name.startswith(".upload")]
        assert (await c.get(f"/media/{name}")).status == 200
        assert (await c.delete(f"/api/media/{name}", headers=H)).status == 200
    run(tmp_path, check)


def test_big_or_turned_pictures_are_made_right(tmp_path):
    from PIL import Image

    async def check(c, admin):
        r = await c.post("/api/media", data=upload(picture("JPEG", (4000, 3000)), "big.jpg"), headers=H)
        with Image.open(tmp_path / "media" / (await r.json())["name"]) as im:
            assert max(im.size) == 1920 and im.size == (1920, 1440)
        # A phone photo stored sideways with an EXIF turn comes out upright.
        r = await c.post("/api/media", data=upload(picture("JPEG", (300, 200), exif_rotate=6), "phone.jpg"), headers=H)
        with Image.open(tmp_path / "media" / (await r.json())["name"]) as im:
            assert im.size == (200, 300) and im.getexif().get(0x0112, 1) == 1
        # Small ones that fit are kept byte for byte.
        small = picture("PNG", (100, 100))
        r = await c.post("/api/media", data=upload(small, "small.png"), headers=H)
        assert (tmp_path / "media" / (await r.json())["name"]).read_bytes() == small
    run(tmp_path, check)


def test_videos_are_optimized_in_the_background(tmp_path):
    import shutil as sh
    import subprocess
    if not sh.which("ffmpeg"):
        return
    clip = tmp_path / "clip.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60:duration=1",
                    "-c:v", "mpeg4", str(clip)], check=True)

    async def check(c, admin):
        r = await c.post("/api/media", data=upload(clip.read_bytes(), "Stage Loop.mov"), headers=H)
        item = await r.json()
        assert r.status == 200 and item == {"name": "Stage-Loop.mp4", "kind": "video", "state": "optimizing"}
        lst = (await (await c.get("/api/media")).json())["media"]
        assert lst[0]["state"] in ("optimizing",) and lst[0]["url"] is None
        await admin.media.wait()
        lst = (await (await c.get("/api/media")).json())["media"]
        assert lst == [{"name": "Stage-Loop.mp4", "url": "/media/Stage-Loop.mp4", "src": "/media/Stage-Loop.mp4",
                        "size": lst[0]["size"], "kind": "video"}]
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=codec_name,width,height,r_frame_rate", "-of", "csv=p=0",
                                str(tmp_path / "media" / "Stage-Loop.mp4")], capture_output=True, text=True).stdout.strip()
        assert probe == "h264,1280,720,30/1"
        assert not list((tmp_path / "media" / ".incoming").iterdir())
        assert (await c.get("/media/Stage-Loop.mp4")).status == 200
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


def test_every_publish_keeps_a_revision(tmp_path):
    async def check(c, admin):
        for text in ("one", "two", "three"):
            tree = {"children": [{"block": "heading", "text": text}]}
            await c.put("/api/layouts/bare/home/draft", json={"tree": tree}, headers=H)
            assert (await c.post("/api/layouts/bare/home/publish", headers=H)).status == 200
        revs = (await (await c.get("/api/layouts/bare/home/revisions")).json())["revisions"]
        assert len(revs) == 3
        newest = await (await c.get(f"/api/layouts/bare/home/revisions/{revs[0]['id']}")).json()
        oldest = await (await c.get(f"/api/layouts/bare/home/revisions/{revs[-1]['id']}")).json()
        assert newest["tree"]["children"][0]["text"] == "three"
        assert oldest["tree"]["children"][0]["text"] == "one"
        assert (await c.get("/api/layouts/bare/home/revisions/..%2F..%2Fsettings")).status == 404
    run(tmp_path, check)


def test_only_the_newest_revisions_are_kept(tmp_path):
    from services.jb_admin.files import Layouts
    layouts = Layouts(tmp_path / "pub", tmp_path / "drafts", tmp_path / "revs")
    for i in range(Layouts.REVISIONS + 5):
        layouts.save_draft("bare", "home", {"children": [], "n": i})
        layouts.publish("bare", "home")
    revs = layouts.list_revisions("bare", "home")
    assert len(revs) == Layouts.REVISIONS
    assert layouts.get_revision("bare", "home", revs[0]["id"])["n"] == Layouts.REVISIONS + 4


def test_theme_cards_use_a_preview_else_the_background_picture(tmp_path):
    from services.jb_admin import theme_store
    added = tmp_path / "added"
    shutil.copytree(SHIPPED / "bare", added / "pic")
    m = json.loads((added / "pic" / "theme.json").read_text())
    m["id"] = "pic"
    (added / "pic" / "theme.json").write_text(json.dumps(m))
    dirs = [SHIPPED, added]
    assert "preview" not in theme_store.describe("pic", dirs, "")
    (added / "pic" / "assets").mkdir()
    (added / "pic" / "assets" / "background.jpg").write_bytes(b"jpg")
    assert theme_store.describe("pic", dirs, "")["preview"] == "/themes/pic/assets/background.jpg"
    (added / "pic" / "preview.png").write_bytes(b"png")
    info = theme_store.describe("pic", dirs, "")
    assert info["preview"] == "/themes/pic/preview.png" and "_manifest" not in info


# Customize

def test_customize_draft_preview_publish_reset(tmp_path):
    async def check(c, admin):
        info = await (await c.get("/api/customize/neon")).json()
        ids = {o["id"] for o in info["options"]}
        assert {"panel", "button", "font", "cover-colors"} <= ids
        assert [s["id"] for s in info["styles"]][:2] == ["midnight", "sunset"]
        assert info["published"] is None and info["draft"] is None

        # Bad values are refused, and nothing is saved.
        r = await c.put("/api/customize/neon/draft", json={"style": "sunset", "values": {"panel": "red"}}, headers=H)
        assert r.status == 400 and (await r.json())["problems"]
        r = await c.put("/api/customize/neon/draft", json={"style": "nope", "values": {}}, headers=H)
        assert r.status == 400
        assert (await (await c.get("/api/customize/neon")).json())["draft"] is None

        # Values equal to the style's are dropped: only real changes are kept.
        r = await c.put("/api/customize/neon/draft", headers=H,
                        json={"style": "sunset", "values": {"panel": "#22102a", "button": "#123456", "roundness": 0.5}})
        assert (await r.json())["saved"] == {"style": "sunset", "values": {"button": "#123456", "roundness": 0.5}}

        # The preview shows the draft; the published look is untouched.
        p = await (await c.get("/api/theme?theme=neon")).json()
        assert p["customize"]["style"] == "sunset"
        assert p["customize"]["values"]["button"] == "#123456"
        assert p["customize"]["values"]["panel"] == "#22102a"
        assert p["customize"]["values"]["text"] == "#fff4ef"
        p = await (await c.get("/api/theme?theme=neon&drafts=0")).json()
        assert p["customize"]["style"] == "midnight" and p["customize"]["values"]["button"] == "#3c56e6"

        assert (await c.post("/api/customize/neon/publish", headers=H)).status == 200
        assert (tmp_path / "styles" / "neon" / "custom.json").is_file()
        assert len((await (await c.get("/api/customize/neon/revisions")).json())["revisions"]) == 1
        assert (await c.post("/api/customize/neon/publish", headers=H)).status == 400   # nothing new

        assert (await c.delete("/api/customize/neon", headers=H)).status == 200
        assert not (tmp_path / "styles" / "neon" / "custom.json").exists()
    run(tmp_path, check)


def test_customize_needs_a_known_theme(tmp_path):
    async def check(c, admin):
        assert (await c.get("/api/customize/..%2Fx")).status in (404, 409)
        assert (await c.put("/api/customize/nope/draft", json={}, headers=H)).status == 409
    run(tmp_path, check)


def test_library_names_come_from_the_bus_snapshots(tmp_path):
    async def check(c, admin):
        assert (await (await c.get("/api/library/artist")).json())["names"] == []
        admin.heard_library("jukebox/jb-library/tracks", {"tracks": [
            {"title": "a", "artist": "beta", "album": "X", "genre": "Rock"},
            {"title": "b", "artist": "Alpha", "album": "Y", "genre": "Rock"},
            {"title": "c", "artist": None}, "junk"]})
        admin.heard_library("jukebox/jb-library/playlists", {"playlists": [{"name": "Road trip"}]})
        assert (await (await c.get("/api/library/artist")).json())["names"] == ["Alpha", "beta"]
        assert (await (await c.get("/api/library/genre")).json())["names"] == ["Rock"]
        assert (await (await c.get("/api/library/playlist")).json())["names"] == ["Road trip"]
        assert (await c.get("/api/library/title")).status == 404
    run(tmp_path, check)


class RecordingBus:
    def __init__(self):
        self.sent = []

    async def publish(self, topic, payload, retain=False):
        self.sent.append((topic, payload))


def test_phones_page_sends_only_phone_commands(tmp_path):
    async def check(client, admin):
        admin.live["phones"] = {"phones": [{"mac": "88:51:F2:56:4B:2B", "name": "Shawn's iPhone", "connected": True}]}
        data = await (await client.get("/api/phones", headers=H)).json()
        assert data["phones"][0]["name"] == "Shawn's iPhone" and data["pairing"]["state"] == "idle"
        # No bus: says so instead of pretending.
        assert (await client.post("/api/phones/pair_start", json={}, headers=H)).status == 503
        admin.bus = RecordingBus()
        assert (await client.post("/api/phones/pair_start", json={}, headers=H)).status == 200
        assert (await client.post("/api/phones/pair_confirm", json={"ok": True}, headers=H)).status == 200
        assert (await client.post("/api/phones/forget", json={"mac": "88:51:f2:56:4b:2b"}, headers=H)).status == 200
        assert (await client.post("/api/phones/forget", json={"mac": "nope"}, headers=H)).status == 400
        assert (await client.post("/api/phones/bluetooth", json={"on": False}, headers=H)).status == 200
        assert (await client.post("/api/phones/reboot", json={}, headers=H)).status == 404
        assert admin.bus.sent == [
            ("jukebox/jb-source-phone/cmd", {"action": "pair_start"}),
            ("jukebox/jb-source-phone/cmd", {"action": "pair_confirm", "ok": True}),
            ("jukebox/jb-source-phone/cmd", {"action": "forget", "mac": "88:51:F2:56:4B:2B"}),
            ("jukebox/jb-source-phone/cmd", {"action": "bluetooth", "on": False}),
        ]
    run(tmp_path, check)


def test_sound_page_sends_only_audio_commands(tmp_path):
    async def check(client, admin):
        admin.live["volume"] = {"volume": 0.2, "muted": False,
                                "whole_home": {"enabled": True, "airplay": "Inside", "airplay_found": True}}
        admin.live["airplay"] = {"receivers": ["Den", "Inside"]}
        data = await (await client.get("/api/audio", headers=H)).json()
        assert data["whole_home"]["airplay"] == "Inside" and data["receivers"] == ["Den", "Inside"]
        assert (await client.post("/api/audio/whole_home", json={"enabled": True}, headers=H)).status == 503
        admin.bus = RecordingBus()
        assert (await client.post("/api/audio/whole_home", json={"enabled": True, "airplay": " Den "}, headers=H)).status == 200
        assert (await client.post("/api/audio/whole_home", json={"enabled": True, "airplay": ""}, headers=H)).status == 400
        assert (await client.post("/api/audio/whole_home_offset", json={"ms": -40}, headers=H)).status == 200
        assert (await client.post("/api/audio/whole_home_offset", json={"ms": 9000}, headers=H)).status == 400
        assert (await client.post("/api/audio/whole_home_resync", json={}, headers=H)).status == 200
        assert (await client.post("/api/audio/airplay_volume", json={"value": 0.555}, headers=H)).status == 200
        assert (await client.post("/api/audio/airplay_volume", json={"value": "loud"}, headers=H)).status == 400
        assert (await client.post("/api/audio/reboot", json={}, headers=H)).status == 404
        assert admin.bus.sent == [
            ("jukebox/jb-audio/cmd", {"action": "whole_home", "enabled": True, "airplay": "Den"}),
            ("jukebox/jb-audio/cmd", {"action": "whole_home_offset", "ms": -40}),
            ("jukebox/jb-audio/cmd", {"action": "whole_home_resync"}),
            ("jukebox/jb-audio/cmd", {"action": "airplay_volume", "value": 0.56}),
        ]
        assert (await client.get("/api/audio", headers=H)).status == 200
    run(tmp_path, check)


def test_sound_page_needs_the_owner(tmp_path):
    async def check(client, admin):
        assert (await client.get("/api/audio", headers=H)).status == 401
        assert (await client.post("/api/audio/whole_home", json={"enabled": False}, headers=H)).status == 401
    run(tmp_path, check, login=False)


def test_phones_page_needs_the_owner(tmp_path):
    async def check(client, admin):
        assert (await client.get("/api/phones", headers=H)).status == 401
        assert (await client.post("/api/phones/pair_start", json={}, headers=H)).status == 401
    run(tmp_path, check, login=False)


def test_extensions_lists_the_inventory_with_licenses(tmp_path):
    async def check(client, admin):
        data = await (await client.get("/api/extensions", headers=H)).json()
        ids = {i["id"] for i in data["items"]}
        assert {"bluez", "mosquitto", "mqtt-js", "lastfm", "neon-media"} <= ids
        for item in data["items"]:
            assert item["license"] and item["kind"] in data["kinds"] and item["duty"] in data["duties"], item["id"]
            if item.get("license_file"):
                assert (Path(main.REPO_DIR) / item["license_file"]).is_file(), item["license_file"]
        # Versions come from this machine: the venv's own packages are found.
        assert next(i for i in data["items"] if i["id"] == "aiohttp")["installed"]
    run(tmp_path, check)


def test_drives_page_combines_the_drive_and_the_library_and_sends_only_drive_commands(tmp_path):
    async def check(client, admin):
        admin.live["drives_local"] = {
            "drives": [{"id": "6AD0", "label": "juk3box HD", "fstype": "exfat", "total": 1000, "used": 250,
                        "free": 750, "images": [{"path": "a.png", "size": 1}], "videos": [],
                        "skipped": [{"path": "x.m4p", "reason": "protected"}]}],
            "tracks": [{"item_path": "drive:6AD0/t.wav", "title": "track01", "artist": None}],
            "ejected": [],
        }
        admin.live["drives_library"] = {"drives": [
            {"id": "6AD0", "label": "juk3box HD", "attached": True, "song_count": 1, "plays": 2,
             "songs": [{"item_path": "drive:6AD0/t.wav", "path": "t.wav", "title": "Moon River", "edited": True}]},
            {"id": "OLD1", "label": "Old stick", "attached": False, "song_count": 9, "plays": 0},
        ]}
        data = await (await client.get("/api/drives", headers=H)).json()
        here, old = data["drives"]
        assert here["state"] == "plugged in" and here["used"] == 250 and here["pictures"] == 1
        assert here["songs"][0]["title"] == "Moon River" and here["songs"][0]["tags"]["title"] == "track01"
        assert old["state"] == "not plugged in" and "songs" not in old
        assert data["settings"] == {"auto": True, "cap_mb": 2048}
        assert (await client.post("/api/drives/eject", json={"id": "6AD0"}, headers=H)).status == 503
        admin.bus = RecordingBus()
        assert (await client.post("/api/drives/eject", json={"id": "6AD0"}, headers=H)).status == 200
        assert (await client.post("/api/drives/eject", json={"id": "../x"}, headers=H)).status == 400
        assert (await client.post("/api/drives/edit", json={"item_path": "drive:6AD0/t.wav",
                                                              "fields": {"artist": "Andy Williams"}}, headers=H)).status == 200
        assert (await client.post("/api/drives/edit", json={"item_path": "/etc/passwd"}, headers=H)).status == 400
        assert (await client.post("/api/drives/forget", json={"id": "OLD1"}, headers=H)).status == 200
        assert (await client.post("/api/drives/format", json={}, headers=H)).status == 404
        assert (await client.post("/api/drives/settings", json={"auto": False, "cap_mb": 512}, headers=H)).status == 200
        assert (await (await client.get("/api/drives", headers=H)).json())["settings"] == {"auto": False, "cap_mb": 512}
        assert admin.bus.sent == [
            ("jukebox/jb-source-local/cmd", {"action": "eject", "id": "6AD0"}),
            ("jukebox/jb-library/cmd", {"action": "edit_drive_song", "item_path": "drive:6AD0/t.wav",
                                        "fields": {"artist": "Andy Williams"}}),
            ("jukebox/jb-library/cmd", {"action": "forget_drive", "id": "OLD1"}),
        ]
    run(tmp_path, check)
