"""jb-admin: the owner's admin, from a phone or laptop on the same network.

Separate from jb-ui on purpose: the kiosk server listens on the jukebox
itself only, this one on the network behind a login, and if this one
crashes the jukebox keeps playing. Like WordPress's dashboard, it's a view
onto the platform's data (docs/contracts/): owner settings, themes, owner
layouts (the page builder) and the media library. It is their only writer.

Routes, all behind the login except the first group:
  /                         the admin app (static/)
  /api/session, /api/setup, /api/login
  /api/...                  settings, themes, layouts, media, status
  /preview                  the real kiosk engine, in preview mode (drafts
                            shown, nothing published): /engine, /api/theme,
                            /themes and /media serve it as jb-ui would
"""

import asyncio
import json
import re
import time
from urllib.parse import unquote
from pathlib import Path

from aiohttp import web

from shared import config, logging as jb_logging
from services.jb_admin import schema, theme_store
from services.jb_admin.auth import Auth
from services.jb_admin.files import Layouts, read_json, write_bytes, write_json
from services.jb_admin.registry import Registry
from services.jb_ui import main as ui
from services.jb_ui import customize, icons, media, themes

SERVICE_NAME = "jb-admin"
HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
REPO_DIR = HERE.parent.parent
UI_DIR = REPO_DIR / "services" / "jb_ui"
DATA = Path("/var/lib/juk3-box")

DEFAULTS = {
    "http_host": "0.0.0.0",
    "http_port": 8080,
    "theme_dirs": [str(REPO_DIR / "themes"), str(DATA / "themes")],
    "settings_path": str(DATA / "settings.json"),
    "owner_layouts_dir": str(DATA / "layouts"),
    "drafts_dir": str(DATA / "drafts"),
    "revisions_dir": str(DATA / "revisions"),
    "media_dir": str(DATA / "media"),
    "owner_styles_dir": str(DATA / "styles"),
    "style_drafts_dir": str(DATA / "style-drafts"),
    "style_revisions_dir": str(DATA / "style-revisions"),
    "media_cache_dir": str(DATA / "cache" / "video"),
    "admin_dir": str(DATA / "admin"),
    "module_dirs": [str(REPO_DIR / "services"), str(DATA / "modules")],
    "default_theme": "bare",
    "max_upload_mb": 600,
}

COOKIE = "jbadmin"
MEDIA_MAX = 25 * 1024 * 1024
MEDIA_MAGIC = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
}

# The admin app: its own scripts only. Frames only from itself (the preview).
ADMIN_CSP = "; ".join([
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https://*.mzstatic.com",
    "font-src 'self'",
    "media-src 'self'",
    "connect-src 'self'",
    "frame-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
])


def preview_csp(host: str) -> str:
    """jb-ui's policy, but the bus is the jukebox's address on the network,
    and only the admin may frame it."""
    bus = f"ws://{host}:9001"
    return ui.CSP.replace("connect-src 'self' ws://localhost:9001 ws://127.0.0.1:9001",
                          f"connect-src 'self' {bus}") + "; frame-ancestors 'self'"


def hostname(request: web.Request) -> str:
    host = request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
    return host if re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", host) else "localhost"


class Admin:
    def __init__(self, cfg: dict, log, bus_enabled: bool = True):
        self.cfg = cfg
        self.log = log
        self.bus_enabled = bus_enabled
        self.theme_dirs = [Path(d) for d in cfg["theme_dirs"]]
        self.added_dir = self.theme_dirs[-1]
        self.settings_path = Path(cfg["settings_path"])
        self.layouts = Layouts(Path(cfg["owner_layouts_dir"]), Path(cfg["drafts_dir"]), Path(cfg["revisions_dir"]))
        # The owner's theme options: one "custom" entry per theme, with the
        # same draft, publish and revisions as layouts.
        self.styles = Layouts(Path(cfg["owner_styles_dir"]), Path(cfg["style_drafts_dir"]), Path(cfg["style_revisions_dir"]))
        self.media_dir = Path(cfg["media_dir"])
        self.auth = Auth(Path(cfg["admin_dir"]))
        self.registry = Registry([Path(d) for d in cfg["module_dirs"]])
        self.videos = media.VideoOptimizer(Path(cfg["media_cache_dir"]), log)
        self.schema_blocks = json.loads((UI_DIR / "block_schema.json").read_text())
        self.live: dict[str, dict] = {}          # latest state off the bus, for the dashboard
        # Names in the library, for settings that pick one (a pinned
        # Featured artist): {"artist": [...], "album": [...], ...}.
        self.library_names: dict[str, list[str]] = {}
        self._library_tracks: list = []
        self._library_playlists: list = []
        self.bus = None
        self._bus_task = None

    # The bus ------------------------------------------------------------

    LIVE_TOPICS = {
        "jukebox/jb-source-phone/now-playing": "now_playing",
        "jukebox/jb-source-phone/connected": "phone",
        "jukebox/jb-audio/state": "volume",
        "jukebox/jb-core/queue": "queue",
    }

    LIBRARY_TOPICS = ("jukebox/jb-library/tracks", "jukebox/jb-library/playlists")

    def heard_library(self, topic: str, payload) -> None:
        """Keeps only the names out of jb-library's snapshots."""
        if not isinstance(payload, dict):
            return
        if topic.endswith("/tracks"):
            self._library_tracks = [t for t in payload.get("tracks") or [] if isinstance(t, dict)]
        else:
            self._library_playlists = [p for p in payload.get("playlists") or [] if isinstance(p, dict)]
        names = {}
        for field in ("artist", "album", "genre"):
            names[field] = sorted({str(t[field]) for t in self._library_tracks if t.get(field)}, key=str.casefold)
        names["playlist"] = [str(p["name"]) for p in self._library_playlists if p.get("name")]
        self.library_names = names

    async def run_bus(self):
        from shared.bus import Bus
        while True:
            try:
                async with Bus(SERVICE_NAME, self.cfg["mqtt_host"], self.cfg["mqtt_port"]) as bus:
                    self.bus = bus
                    self.log.info("connected to the bus")
                    heartbeat = asyncio.create_task(bus.run_heartbeat())
                    try:
                        for topic in ("jukebox/+/heartbeat", "jukebox/+/status", "jukebox/analysis/+/status",
                                      *self.LIVE_TOPICS, *self.LIBRARY_TOPICS):
                            await bus._client.subscribe(topic)
                        messages = bus._iter_messages()
                        async for topic, payload in messages:
                            if topic in self.LIVE_TOPICS:
                                self.live[self.LIVE_TOPICS[topic]] = payload
                            elif topic in self.LIBRARY_TOPICS:
                                self.heard_library(topic, payload)
                            else:
                                self.registry.heard(topic, payload)
                    finally:
                        heartbeat.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # the bus being away must never stop the admin
                self.log.warning("bus: %s; retrying", e)
            self.bus = None
            await asyncio.sleep(3)

    async def announce(self, *what: str) -> None:
        """Tell the platform the owner changed something (the cabinet
        reloads on it). Not retained: a module starting later reads files."""
        if self.bus is None:
            self.log.warning("bus down: the cabinet won't reload until it restarts")
            return
        try:
            await self.bus.publish("jukebox/jb-admin/changed", {"what": list(what), "at": time.time()})
        except Exception as e:
            self.log.warning("couldn't announce the change: %s", e)

    # Helpers --------------------------------------------------------------

    def raw_settings(self) -> dict:
        data = read_json(self.settings_path, {})
        return data if isinstance(data, dict) else {}

    def active_theme(self) -> str:
        return self.raw_settings().get("theme") or self.cfg["default_theme"]

    def token(self, request) -> str | None:
        return request.cookies.get(COOKIE)

    def logged_in(self, request) -> bool:
        return self.auth.valid(self.token(request))

    @staticmethod
    def client(request) -> str:
        return request.remote or "?"

    def set_cookie(self, response: web.Response, token: str) -> None:
        response.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True, samesite="Strict", path="/")

    def load_theme(self, theme_id: str) -> themes.Theme:
        try:
            return themes.load_theme(theme_id, self.theme_dirs)
        except themes.ThemeError as e:
            raise web.HTTPConflict(text=json.dumps({"error": "That theme can't load.", "problems": e.problems}),
                                   content_type="application/json")

    # Login ------------------------------------------------------------------

    async def session(self, request):
        return web.json_response({"setup_needed": not self.auth.has_password(),
                                  "logged_in": self.logged_in(request)})

    async def setup(self, request):
        if self.auth.has_password():
            raise error(403, "The admin is already set up. Log in instead.")
        body = await json_body(request)
        try:
            self.auth.set_password(body.get("password"))
        except ValueError as e:
            raise error(400, str(e))
        self.log.info("owner password set from %s", self.client(request))
        response = web.json_response({"ok": True})
        self.set_cookie(response, self.auth.open_session())
        return response

    async def login(self, request):
        who = self.client(request)
        wait = self.auth.locked_for(who)
        if wait:
            raise error(429, f"Too many tries. Wait {int(wait) + 1} seconds.")
        body = await json_body(request)
        ok = await asyncio.to_thread(self.auth.check_password, body.get("password"))
        if not ok:
            self.auth.failed(who)
            self.log.warning("failed login from %s", who)
            await asyncio.sleep(0.5)
            raise error(401, "That password isn't right.")
        self.auth.succeeded(who)
        response = web.json_response({"ok": True})
        self.set_cookie(response, self.auth.open_session())
        return response

    async def logout(self, request):
        self.auth.close_session(self.token(request))
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE, path="/")
        return response

    async def change_password(self, request):
        body = await json_body(request)
        if not await asyncio.to_thread(self.auth.check_password, body.get("current")):
            raise error(401, "Your current password isn't right.")
        try:
            self.auth.set_password(body.get("new"))
        except ValueError as e:
            raise error(400, str(e))
        self.auth.close_all()          # every other device logs in again
        response = web.json_response({"ok": True})
        self.set_cookie(response, self.auth.open_session())
        return response

    # Status ------------------------------------------------------------------

    async def systemd_states(self) -> dict[str, str]:
        units = [m["id"] for m in self.registry.view()]
        if not units:
            return {}
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "show", "-p", "Id", "-p", "ActiveState", *[f"{u}.service" for u in units],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
        except (OSError, asyncio.TimeoutError):
            return {}
        states, unit = {}, None
        for line in out.decode().splitlines():
            if line.startswith("Id="):
                unit = line[3:].removesuffix(".service")
            elif line.startswith("ActiveState=") and unit:
                states[unit] = line[len("ActiveState="):]
        return states

    async def status(self, request):
        return web.json_response({
            "modules": self.registry.view(await self.systemd_states()),
            "live": self.live,
            "bus": self.bus is not None,
            "theme": self.active_theme(),
            "theme_name": self.theme_name(self.active_theme()),
        })

    def theme_name(self, theme_id: str) -> str:
        try:
            return themes.load_theme(theme_id, self.theme_dirs).manifest.get("name") or theme_id
        except themes.ThemeError:
            return theme_id

    # Settings -----------------------------------------------------------------

    async def get_settings(self, request):
        return web.json_response({"values": schema.current(self.raw_settings()), "schema": schema.SCHEMA})

    async def put_settings(self, request):
        body = await json_body(request)
        clean, problems = schema.validate(body.get("values"))
        if problems:
            raise error(400, "Some settings can't be used.", problems=problems)
        raw = self.raw_settings()
        raw.update(clean)                      # unknown keys are kept
        write_json(self.settings_path, raw)
        await self.announce("settings")
        return web.json_response({"values": schema.current(raw)})

    # Themes ----------------------------------------------------------------------

    async def list_themes(self, request):
        return web.json_response({"themes": theme_store.list_all(self.theme_dirs, self.active_theme()),
                                  "active": self.active_theme()})

    async def activate_theme(self, request):
        body = await json_body(request)
        theme_id = body.get("id")
        if not isinstance(theme_id, str):
            raise error(400, "Which theme?")
        self.load_theme(theme_id)              # refuses one that can't load
        raw = self.raw_settings()
        raw["theme"] = theme_id
        write_json(self.settings_path, raw)
        await self.announce("theme")
        return web.json_response({"active": theme_id})

    async def upload_theme(self, request):
        replace = request.query.get("replace") == "1"
        tmp = await save_upload(request, Path(self.cfg["admin_dir"]) / "uploads", ".zip",
                                self.cfg["max_upload_mb"] * 1024 * 1024)
        try:
            info = await asyncio.to_thread(theme_store.install_zip, tmp, self.added_dir, self.theme_dirs, replace)
        except theme_store.InstallError as e:
            raise error(400, str(e))
        finally:
            tmp.unlink(missing_ok=True)
        self.log.info("installed theme %s", info["id"])
        if info["id"] == self.active_theme():
            await self.announce("theme")
        return web.json_response(info)

    async def delete_theme(self, request):
        theme_id = request.match_info["id"]
        try:
            theme_store.delete(theme_id, self.added_dir, self.theme_dirs, self.active_theme())
        except theme_store.InstallError as e:
            raise error(400, str(e))
        self.layouts.forget_theme(theme_id)
        self.styles.forget_theme(theme_id)
        return web.json_response({"ok": True})

    # Layouts (the page builder) ------------------------------------------------

    async def blocks(self, request):
        return web.json_response(self.schema_blocks)

    async def theme_layouts(self, request):
        theme_id = request.match_info["theme"]
        theme = self.load_theme(theme_id)
        touched = self.layouts.names(theme_id)
        names = list(dict.fromkeys([*themes.REQUIRED_LAYOUTS, *theme.layouts, *touched]))
        out = []
        for name in names:
            t = touched.get(name, {})
            out.append({"name": name, "in_theme": name in theme.layouts,
                        "published": t.get("published", False), "draft": t.get("draft", False)})
        return web.json_response({"theme": theme_id, "name": theme.manifest.get("name"), "layouts": out})

    async def get_layout(self, request):
        theme_id, name = request.match_info["theme"], request.match_info["name"]
        theme = self.load_theme(theme_id)
        try:
            published, draft = self.layouts.get(theme_id, name)
        except ValueError:
            raise web.HTTPNotFound()
        return web.json_response({"theme": theme.layouts.get(name), "published": published, "draft": draft})

    async def save_draft(self, request):
        theme_id, name = request.match_info["theme"], request.match_info["name"]
        self.load_theme(theme_id)
        body = await json_body(request)
        tree = body.get("tree")
        problems, warnings = themes.check_layout(name, tree)
        if problems:
            raise error(400, "The layout can't be used.", problems=problems)
        try:
            self.layouts.save_draft(theme_id, name, tree)
        except ValueError:
            raise web.HTTPNotFound()
        return web.json_response({"ok": True, "warnings": warnings})

    async def discard_draft(self, request):
        theme_id, name = request.match_info["theme"], request.match_info["name"]
        try:
            self.layouts.discard_draft(theme_id, name)
        except ValueError:
            raise web.HTTPNotFound()
        return web.json_response({"ok": True})

    async def publish_layout(self, request):
        theme_id, name = request.match_info["theme"], request.match_info["name"]
        try:
            draft = self.layouts.get(theme_id, name)[1]
        except ValueError:
            raise web.HTTPNotFound()
        if draft is None:
            raise error(400, "There's nothing new to publish.")
        problems, _ = themes.check_layout(name, draft)
        if problems:
            raise error(400, "The layout can't be used.", problems=problems)
        self.layouts.publish(theme_id, name)
        self.log.info("published %s/%s", theme_id, name)
        if theme_id == self.active_theme():
            await self.announce("layouts")
        return web.json_response({"ok": True})

    async def reset_layout(self, request):
        theme_id, name = request.match_info["theme"], request.match_info["name"]
        try:
            self.layouts.reset(theme_id, name)
        except ValueError:
            raise web.HTTPNotFound()
        if theme_id == self.active_theme():
            await self.announce("layouts")
        return web.json_response({"ok": True})

    async def library_list(self, request):
        field = request.match_info["field"]
        if field not in ("artist", "album", "genre", "playlist"):
            raise web.HTTPNotFound()
        return web.json_response({"names": self.library_names.get(field, []), "bus": self.bus is not None})

    # Customize: a theme's global options ------------------------------------------

    STYLE = "custom"

    async def get_customize(self, request):
        theme_id = request.match_info["theme"]
        theme = self.load_theme(theme_id)
        published, draft = self.styles.get(theme_id, self.STYLE)
        return web.json_response({
            "theme": theme_id,
            "name": theme.manifest.get("name"),
            "active": theme_id == self.active_theme(),
            "options": customize.options_of(theme.manifest),
            "styles": customize.styles_of(theme.manifest),
            "groups": list(customize.GROUPS),
            "fonts": customize.font_library(),
            "published": published,
            "draft": draft,
        })

    async def save_customize_draft(self, request):
        theme_id = request.match_info["theme"]
        theme = self.load_theme(theme_id)
        clean, problems = customize.clean(theme.manifest, theme.root, await json_body(request))
        if problems:
            raise error(400, "Some choices can't be used.", problems=problems)
        self.styles.save_draft(theme_id, self.STYLE, clean)
        return web.json_response({"ok": True, "saved": clean})

    async def discard_customize_draft(self, request):
        self.load_theme(request.match_info["theme"])
        self.styles.discard_draft(request.match_info["theme"], self.STYLE)
        return web.json_response({"ok": True})

    async def publish_customize(self, request):
        theme_id = request.match_info["theme"]
        theme = self.load_theme(theme_id)
        draft = self.styles.get(theme_id, self.STYLE)[1]
        if draft is None:
            raise error(400, "There's nothing new to publish.")
        _, problems = customize.clean(theme.manifest, theme.root, draft)
        if problems:
            raise error(400, "Some choices can't be used.", problems=problems)
        self.styles.publish(theme_id, self.STYLE)
        self.log.info("published %s options", theme_id)
        if theme_id == self.active_theme():
            await self.announce("customize")
        return web.json_response({"ok": True})

    async def reset_customize(self, request):
        theme_id = request.match_info["theme"]
        self.load_theme(theme_id)
        self.styles.reset(theme_id, self.STYLE)
        if theme_id == self.active_theme():
            await self.announce("customize")
        return web.json_response({"ok": True})

    async def customize_revisions(self, request):
        theme_id = request.match_info["theme"]
        self.load_theme(theme_id)
        return web.json_response({"revisions": self.styles.list_revisions(theme_id, self.STYLE)})

    async def customize_revision(self, request):
        data = self.styles.get_revision(request.match_info["theme"], self.STYLE, request.match_info["rev"])
        if data is None:
            raise web.HTTPNotFound()
        return web.json_response({"values": data})

    async def theme_pictures(self, request):
        """Pictures inside a theme's folder, for image and logo settings."""
        theme_id = request.match_info["id"]
        root = themes.find_theme_dir(theme_id, self.theme_dirs)
        if root is None:
            raise web.HTTPNotFound()
        found = []
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg") \
                    and not rel.startswith("assets/fonts/") and themes.safe_path(root, rel):
                found.append({"name": rel, "src": rel, "url": f"/themes/{theme_id}/{rel}"})
        return web.json_response({"pictures": found[:500]})

    async def icon_set(self, request):
        """Icons for the admin's own buttons, from the bundled Lucide set."""
        names = request.query.get("names", "").split(",")[:200]
        out = {}
        for n in names:
            if re.fullmatch(r"[a-z0-9-]{1,60}", n) and (s := icons.svg(f"lucide:{n}")):
                out[n] = s
        response = web.json_response(out)
        return response

    async def revisions(self, request):
        theme_id, name = request.match_info["theme"], request.match_info["name"]
        return web.json_response({"revisions": self.layouts.list_revisions(theme_id, name)})

    async def revision(self, request):
        theme_id, name = request.match_info["theme"], request.match_info["name"]
        tree = self.layouts.get_revision(theme_id, name, request.match_info["rev"])
        if tree is None:
            raise web.HTTPNotFound()
        return web.json_response({"tree": tree})

    # Media library -----------------------------------------------------------------

    async def list_media(self, request):
        items = []
        if self.media_dir.is_dir():
            for p in sorted(self.media_dir.iterdir(), key=lambda p: -p.stat().st_mtime):
                if ui.media_path(self.media_dir, p.name):
                    items.append({"name": p.name, "url": f"/media/{p.name}", "src": f"/media/{p.name}",
                                  "size": p.stat().st_size})
        return web.json_response({"media": items})

    async def upload_media(self, request):
        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "file":
            raise error(400, "Choose a picture to upload.")
        data = bytearray()
        while chunk := await part.read_chunk(256 * 1024):
            data += chunk
            if len(data) > MEDIA_MAX:
                raise error(400, f"Pictures can be up to {MEDIA_MAX // 1048576} MB.")
        suffix = next((s for magic, s in MEDIA_MAGIC.items() if data.startswith(magic)), None)
        if suffix is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            suffix = ".webp"
        if suffix is None:
            raise error(400, "That isn't a JPEG, PNG, GIF or WebP picture.")
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(unquote(part.filename or "picture")).stem).strip("-")[:60] or "picture"
        name = f"{stem}{suffix}"
        n = 2
        while (self.media_dir / name).exists():
            name = f"{stem}-{n}{suffix}"
            n += 1
        write_bytes(self.media_dir / name, bytes(data))
        return web.json_response({"name": name, "url": f"/media/{name}", "src": f"/media/{name}"})

    async def delete_media(self, request):
        path = ui.media_path(self.media_dir, request.match_info["name"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        path.unlink()
        return web.json_response({"ok": True})

    # Preview: the kiosk engine, served as jb-ui would ------------------------------

    async def preview_page(self, request):
        # Always in preview mode: without ?preview the engine would act on the
        # jukebox (a keypad pick seen here and on the cabinet plays twice).
        if "preview" not in request.query:
            raise web.HTTPFound(request.rel_url.update_query(preview="1"))
        return web.FileResponse(UI_DIR / "engine" / "index.html")

    async def preview_theme(self, request):
        theme_id = request.query.get("theme") or self.active_theme()
        theme = self.load_theme(theme_id)
        folders = []
        if request.query.get("drafts") != "0":
            folders.append(("draft", Path(self.cfg["drafts_dir"]) / theme_id))
        folders.append(("owner", Path(self.cfg["owner_layouts_dir"]) / theme_id))
        settings = ui.owner_settings(self.raw_settings())
        published, draft = self.styles.get(theme_id, self.STYLE)
        style = (draft if request.query.get("drafts") != "0" and draft is not None else published)
        return web.json_response(ui.theme_payload(theme, None, settings, folders, self.log, style))

    async def theme_file(self, request):
        root = themes.find_theme_dir(request.match_info["theme_id"], self.theme_dirs)
        if root is None:
            raise web.HTTPNotFound()
        path = themes.safe_path(root, request.match_info["path"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        if self.videos.is_video(path):
            # The kiosk's optimized copy if there is one; never start an
            # encode from here (jb-ui does that, at the lowest priority).
            copy = self.videos.copy_path(path)
            if copy.is_file():
                path = copy
        return web.FileResponse(path)

    async def media_file(self, request):
        path = ui.media_path(self.media_dir, request.match_info["name"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def index(self, request):
        return web.FileResponse(STATIC_DIR / "index.html")


def error(status: int, message: str, **extra) -> web.HTTPException:
    cls = {400: web.HTTPBadRequest, 401: web.HTTPUnauthorized, 403: web.HTTPForbidden,
           404: web.HTTPNotFound, 409: web.HTTPConflict, 429: web.HTTPTooManyRequests}[status]
    return cls(text=json.dumps({"error": message, **extra}), content_type="application/json")


async def json_body(request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise error(400, "Expected JSON.")
    if not isinstance(body, dict):
        raise error(400, "Expected a JSON object.")
    return body


async def save_upload(request, folder: Path, suffix: str, limit: int) -> Path:
    """Streams the multipart field "file" to a temporary file."""
    import tempfile
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file":
        raise error(400, "Choose a file to upload.")
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=folder, suffix=suffix)
    path = Path(name)
    size = 0
    try:
        with open(fd, "wb") as out:
            while chunk := await part.read_chunk(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise error(400, f"Too big: the limit is {limit // 1048576} MB.")
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


ADMIN_KEY = web.AppKey("admin", object)


PUBLIC = {("GET", "/"), ("GET", "/api/icons"), ("GET", "/api/session"), ("POST", "/api/setup"), ("POST", "/api/login")}


def build_app(cfg: dict, log, bus_enabled: bool = True) -> web.Application:
    admin = Admin(cfg, log, bus_enabled)

    @web.middleware
    async def guard(request, handler):
        path = request.path
        public = (request.method, path) in PUBLIC or path.startswith("/static/")
        # Changes need a header a page on another site can't send without
        # asking first (CORS), on top of the SameSite cookie.
        if request.method not in ("GET", "HEAD") and request.headers.get("X-JB-Admin") != "1":
            raise error(403, "Missing the admin header.")
        if not public and not admin.logged_in(request):
            if path.startswith("/api/"):
                raise error(401, "Log in first.")
            raise web.HTTPFound("/")
        response = await handler(request)
        preview = path in ("/preview", "/api/theme") or path.startswith(("/engine/", "/themes/", "/media/"))
        response.headers["Content-Security-Policy"] = preview_csp(hostname(request)) if preview else ADMIN_CSP
        response.headers["Cache-Control"] = "no-store" if path.startswith("/api/") else "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    app = web.Application(middlewares=[guard], client_max_size=cfg["max_upload_mb"] * 1024 * 1024)
    r = app.router
    r.add_get("/", admin.index)
    r.add_static("/static/", STATIC_DIR)
    r.add_get("/api/session", admin.session)
    r.add_post("/api/setup", admin.setup)
    r.add_post("/api/login", admin.login)
    r.add_post("/api/logout", admin.logout)
    r.add_post("/api/password", admin.change_password)
    r.add_get("/api/status", admin.status)
    r.add_get("/api/settings", admin.get_settings)
    r.add_put("/api/settings", admin.put_settings)
    r.add_get("/api/themes", admin.list_themes)
    r.add_post("/api/themes/activate", admin.activate_theme)
    r.add_post("/api/themes/upload", admin.upload_theme)
    r.add_delete("/api/themes/{id}", admin.delete_theme)
    r.add_get("/api/blocks", admin.blocks)
    r.add_get("/api/icons", admin.icon_set)
    r.add_get("/api/themes/{id}/pictures", admin.theme_pictures)
    r.add_get("/api/layouts/{theme}", admin.theme_layouts)
    r.add_get("/api/layouts/{theme}/{name}", admin.get_layout)
    r.add_put("/api/layouts/{theme}/{name}/draft", admin.save_draft)
    r.add_delete("/api/layouts/{theme}/{name}/draft", admin.discard_draft)
    r.add_post("/api/layouts/{theme}/{name}/publish", admin.publish_layout)
    r.add_delete("/api/layouts/{theme}/{name}", admin.reset_layout)
    r.add_get("/api/layouts/{theme}/{name}/revisions", admin.revisions)
    r.add_get("/api/layouts/{theme}/{name}/revisions/{rev}", admin.revision)
    r.add_get("/api/library/{field}", admin.library_list)
    r.add_get("/api/customize/{theme}", admin.get_customize)
    r.add_put("/api/customize/{theme}/draft", admin.save_customize_draft)
    r.add_delete("/api/customize/{theme}/draft", admin.discard_customize_draft)
    r.add_post("/api/customize/{theme}/publish", admin.publish_customize)
    r.add_delete("/api/customize/{theme}", admin.reset_customize)
    r.add_get("/api/customize/{theme}/revisions", admin.customize_revisions)
    r.add_get("/api/customize/{theme}/revisions/{rev}", admin.customize_revision)
    r.add_get("/api/media", admin.list_media)
    r.add_post("/api/media", admin.upload_media)
    r.add_delete("/api/media/{name}", admin.delete_media)
    r.add_get("/preview", admin.preview_page)
    r.add_get("/api/theme", admin.preview_theme)
    r.add_get("/themes/{theme_id}/{path:.+}", admin.theme_file)
    r.add_get("/media/{name}", admin.media_file)
    r.add_static("/engine/", UI_DIR / "engine")

    async def start_bus(app):
        if bus_enabled:
            admin._bus_task = asyncio.create_task(admin.run_bus())

    async def stop_bus(app):
        if admin._bus_task:
            admin._bus_task.cancel()

    app.on_startup.append(start_bus)
    app.on_cleanup.append(stop_bus)
    app[ADMIN_KEY] = admin
    return app


def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = {**DEFAULTS, **config.load(SERVICE_NAME)}
    log.info("admin on http://%s:%s", cfg["http_host"], cfg["http_port"])
    web.run_app(build_app(cfg, log), host=cfg["http_host"], port=cfg["http_port"], print=None)


if __name__ == "__main__":
    main()
