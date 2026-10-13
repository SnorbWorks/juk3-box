import asyncio
import json
import logging
import shutil
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from services.jb_ui import main

SHIPPED = Path(__file__).resolve().parents[3] / "themes"


def run(tmp_path, check, theme=None, setup=None):
    """Start jb-ui against tmp settings and an empty added-themes folder,
    then run `check(client)`."""
    settings = tmp_path / "settings.json"
    if theme:
        settings.write_text(json.dumps({"theme": theme}))
    added = tmp_path / "added"
    added.mkdir()
    if setup:
        setup(added)
    cfg = {**main.DEFAULTS, "theme_dirs": [str(SHIPPED), str(added)], "settings_path": str(settings),
           "owner_layouts_dir": str(tmp_path / "layouts")}

    async def go():
        async with TestClient(TestServer(main.build_app(cfg, logging.getLogger("test")))) as client:
            await check(client)
    asyncio.run(go())


def test_engine_page_has_the_security_policy(tmp_path):
    async def check(client):
        r = await client.get("/")
        assert r.status == 200
        assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    run(tmp_path, check)


def test_default_theme(tmp_path):
    async def check(client):
        data = await (await client.get("/api/theme")).json()
        assert data["id"] == "bare" and data["fallback"] is None
        assert data["base"] == "/themes/bare/"
        assert "frame" in data["layouts"]
    run(tmp_path, check)


def test_broken_chosen_theme_falls_back_to_bare(tmp_path):
    def setup(added):
        shutil.copytree(SHIPPED / "bare", added / "broken")  # id mismatch: invalid

    async def check(client):
        data = await (await client.get("/api/theme")).json()
        assert data["id"] == "bare"
        assert data["fallback"]["wanted"] == "broken"
        assert any("doesn't match its folder name" in p for p in data["fallback"]["problems"])
    run(tmp_path, check, theme="broken", setup=setup)


def test_theme_files_are_served_but_cannot_escape(tmp_path):
    async def check(client):
        assert (await client.get("/themes/bare/theme.css")).status == 200
        assert (await client.get("/themes/bare/%2e%2e/%2e%2e/services/jb_ui/main.py")).status == 404
        assert (await client.get("/themes/nope/theme.css")).status == 404
    run(tmp_path, check)


def test_legacy_page_has_no_csp(tmp_path):
    async def check(client):
        r = await client.get("/legacy/index.html")
        assert r.status == 200
        assert "Content-Security-Policy" not in r.headers
    run(tmp_path, check)


def test_owner_nav_settings_are_cleaned():
    assert main.owner_settings({})["nav"] == main.DEFAULT_NAV
    assert main.owner_settings({"nav": ["settings", "bogus", "home", "home"]})["nav"] == ["settings", "home"]
    assert main.owner_settings({"nav": ["bogus"]})["nav"] == main.DEFAULT_NAV
    assert main.owner_settings({"nav": "home"})["nav"] == main.DEFAULT_NAV


def test_api_includes_settings_and_icons(tmp_path):
    def setup(added):
        d = added / "iconic"
        shutil.copytree(SHIPPED / "bare", d)
        m = json.loads((d / "theme.json").read_text())
        m["id"] = "iconic"
        (d / "theme.json").write_text(json.dumps(m))
        (d / "assets" / "icons").mkdir(parents=True)
        (d / "assets" / "icons" / "home.svg").write_text("<svg></svg>")
        (d / "assets" / "icons" / "notes.txt").write_text("not an icon")

    async def check(client):
        data = await (await client.get("/api/theme")).json()
        assert data["id"] == "iconic"
        assert data["icons"]["home"] == {"url": "/themes/iconic/assets/icons/home.svg"}  # the theme's file wins
        assert "<svg" in data["icons"]["volume"]["svg"]                              # engine default
        assert data["icons"]["now-playing"] == {"builtin": "eq"}
        assert data["settings"]["nav"] == main.DEFAULT_NAV
        assert "nav-button" in data["blocks"] and "popup-volume" in data["blocks"]
    run(tmp_path, check, theme="iconic", setup=setup)


def test_owner_board_and_idle_settings_are_cleaned():
    s = main.owner_settings({})
    assert (s["songs_per_page"], s["idle_home_minutes"]) == (100, 5)
    s = main.owner_settings({"songs_per_page": 50, "idle_home_minutes": 0})
    assert (s["songs_per_page"], s["idle_home_minutes"]) == (50, 0)
    for bad in (55, 0, 110, "50", True, 50.0):
        assert main.owner_settings({"songs_per_page": bad})["songs_per_page"] == 100, bad
    for bad in (-1, "5", None, False):
        assert main.owner_settings({"idle_home_minutes": bad})["idle_home_minutes"] == 5, bad


def test_owner_layouts_replace_the_themes_and_bad_ones_are_ignored(tmp_path):
    owner = tmp_path / "layouts" / "bare"
    owner.mkdir(parents=True)
    home = {"class": "mine", "children": [{"block": "section", "type": "recents", "limit": 4}]}
    (owner / "home.json").write_text(json.dumps(home))
    (owner / "all-songs.json").write_text('{"children": [{"block": "screen"}]}')   # screen outside the frame
    (owner / "frame.json").write_text("{not json")

    async def check(client):
        data = await (await client.get("/api/theme")).json()
        assert data["layouts"]["home"] == home
        assert data["layout_origins"] == {"frame": "theme", "home": "owner", "all-songs": "theme"}
        assert data["layouts"]["frame"]["children"]          # the theme's frame, still whole
        assert any("all-songs" in w for w in data["warnings"])
        assert any("frame" in w for w in data["warnings"])
    run(tmp_path, check)


def test_media_library_serves_plain_pictures_only(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "photo.jpg").write_bytes(b"jpg")
    (media / "evil.svg").write_text("<svg/>")
    (tmp_path / "secret.jpg").write_bytes(b"no")
    settings = tmp_path / "settings.json"
    cfg = {**main.DEFAULTS, "theme_dirs": [str(SHIPPED)], "settings_path": str(settings),
           "owner_layouts_dir": str(tmp_path / "layouts"), "media_dir": str(media)}

    async def go():
        async with TestClient(TestServer(main.build_app(cfg, logging.getLogger("test")))) as client:
            assert (await client.get("/media/photo.jpg")).status == 200
            assert (await client.get("/media/evil.svg")).status == 404
            assert (await client.get("/media/..%2Fsecret.jpg")).status == 404
            assert (await client.get("/media/nope.jpg")).status == 404
    asyncio.run(go())
