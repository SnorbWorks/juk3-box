"""jb-ui: serves the kiosk's UI engine and the active theme.

Listens on the Pi itself only (127.0.0.1): the kiosk browser is the only
client. Phone/laptop admin is jb-admin's job, on its own port.

Routes:
  /                    the engine page
  /engine/...          engine scripts and styles
  /api/theme           the active theme: manifest, layouts and block
                       markup, validated and cleaned
  /themes/<id>/...     files from that theme's folder
  /media/<file>        pictures the owner uploaded (jb-admin's media library)
  /legacy/...          the first-build song board, kept until the engine
                       can do everything it did
"""

import json
import re
from pathlib import Path

from aiohttp import web

from shared import config, logging as jb_logging
from services.jb_ui import customize, icons, markup, media, module_blocks, themes

SERVICE_NAME = "jb-ui"
HERE = Path(__file__).resolve().parent
ENGINE_DIR = HERE / "engine"
LEGACY_DIR = HERE / "static"
REPO_DIR = HERE.parent.parent

DEFAULTS = {
    "http_host": "127.0.0.1",
    "http_port": 8090,
    # Themes that ship with JUKE.BOX first, then themes the owner added
    # (outside /opt/juk3-box, which every deploy replaces).
    "theme_dirs": [str(REPO_DIR / "themes"), "/var/lib/juk3-box/themes"],
    # Owner choices: active theme, nav buttons (later Home sections, ...).
    "settings_path": "/var/lib/juk3-box/settings.json",
    "default_theme": "bare",
    # Always shipped; used when the chosen theme can't load.
    "fallback_theme": "bare",
    # Background videos re-encoded for the hardware (see media.py).
    "media_cache_dir": "/var/lib/juk3-box/cache/video",
    # The owner's published layouts, per theme (written by jb-admin; see
    # docs/contracts/configuration.md).
    "owner_layouts_dir": "/var/lib/juk3-box/layouts",
    # Pictures the owner uploaded in the admin (the media library), for
    # image and logo blocks: "/media/<file>".
    "media_dir": "/var/lib/juk3-box/media",
    # The owner's published theme options (colors, fonts...), per theme:
    # <dir>/<theme>/custom.json (written by jb-admin).
    "owner_styles_dir": "/var/lib/juk3-box/styles",
    # Where modules are, for the blocks they add (docs/contracts/extensions.md):
    # the built-in ones, then the owner's installed ones.
    "module_dirs": [str(REPO_DIR / "services"), "/var/lib/juk3-box/modules"],
}

# Themes are data only: the engine's own scripts run, nothing else does.
# Cover art comes from Apple's image servers (iTunes lookups) until
# jb-library caches it locally.
CSP = "; ".join([
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://*.mzstatic.com",
    "font-src 'self'",
    "media-src 'self'",
    "connect-src 'self' ws://localhost:9001 ws://127.0.0.1:9001",
    "base-uri 'none'",
    "form-action 'none'",
])


def read_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# Nav buttons the engine knows (docs/theme-format.md, "Nav buttons").
# "now-playing" is a nav button an owner can add, but by default Now
# Playing is the header of the Now Playing card instead.
NAV_BUTTONS = ("home", "volume", "shuffle", "all-songs", "settings", "now-playing")
DEFAULT_NAV = ["home", "volume", "shuffle", "all-songs", "settings"]


# No SVG: it can carry scripts. Videos are MP4 only: jb-admin converts every
# uploaded video to the Pi's profile (services/jb_admin/media_lib.py).
MEDIA_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4")
MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def media_path(folder: Path, name: str) -> Path | None:
    """A file in the owner's media library, or None for anything that
    isn't a plain picture name (no folders, no escaping)."""
    if not MEDIA_NAME_RE.match(name) or ".." in name or Path(name).suffix.lower() not in MEDIA_TYPES:
        return None
    return folder / name


def theme_payload(theme: themes.Theme, fallback: dict | None, settings: dict,
                  layout_folders: list, log, style: dict | None = None,
                  module_folders: list | None = None) -> dict:
    """What /api/theme sends the engine: the theme (its manifest, layouts
    with the owner's laid over them, block markup, icons, its options with
    the owner's choices) and the owner's settings. Shared with jb-admin's
    preview, which adds drafts to `layout_folders` and passes the draft
    `style`."""
    layouts, origins, problems = themes.owner_layouts(theme, layout_folders)
    for p in problems:
        log.warning("theme %s: using the theme's layout instead: %s", theme.id, p)
    custom, style_problems = customize.resolve(theme.manifest, theme.root, style)
    for p in style_problems:
        log.warning("theme %s options: using the theme's value instead: %s", theme.id, p)
    blocks_from_modules, block_problems = module_blocks.module_blocks(module_folders or [])
    for p in block_problems:
        log.warning("skipping a module block: %s", p)
    base = f"/themes/{theme.id}/"
    return {
        "id": theme.id,
        "base": base,
        "manifest": theme.manifest,
        "layouts": layouts,
        "layout_origins": origins,
        "blocks": markup.load_blocks(ENGINE_DIR / "blocks", theme.root, base),
        "icons": icons.resolve(
            theme.manifest.get("icons", {}),
            {name: base + path for name, path in themes.icons(theme.root).items()},
        ),
        "browse_images": {name: base + path for name, path in themes.browse_images(theme.root).items()},
        "settings": settings,
        "customize": custom,
        "card_icons": icons.card_icons(),
        "module_blocks": blocks_from_modules,
        "fonts": [{**f, "src": f"/engine/fonts/{f['src']}"} for f in customize.font_library()],
        "warnings": theme.warnings + problems + style_problems,
        "fallback": fallback,
    }


def owner_settings(raw: dict) -> dict:
    """The owner's choices the engine needs, with anything unusable
    replaced by the default."""
    nav = raw.get("nav")
    nav = list(dict.fromkeys(b for b in nav if b in NAV_BUTTONS)) if isinstance(nav, list) else None
    if not nav:
        nav = DEFAULT_NAV

    # Songs per board page: 10 to 100 in steps of 10, so every letter gets
    # the same number of slots and the codes always match the keypad.
    per_page = raw.get("songs_per_page")
    if not (isinstance(per_page, int) and not isinstance(per_page, bool) and 10 <= per_page <= 100 and per_page % 10 == 0):
        per_page = 100

    # Idle minutes, with nothing playing, before the screen goes back to
    # Home. 0 = never.
    idle = raw.get("idle_home_minutes")
    if not (isinstance(idle, (int, float)) and not isinstance(idle, bool) and idle >= 0):
        idle = 5

    return {
        "nav": nav,
        "songs_per_page": per_page,
        "idle_home_minutes": idle,
    }


class App:
    def __init__(self, cfg: dict, log):
        self.cfg = cfg
        self.log = log
        self.theme_dirs = [Path(d) for d in cfg["theme_dirs"]]
        self.settings_path = Path(cfg["settings_path"])
        self.videos = media.VideoOptimizer(Path(cfg["media_cache_dir"]), log)

    def active_theme(self) -> tuple[themes.Theme, dict | None]:
        """The theme to show, plus details if we had to fall back."""
        wanted = read_settings(self.settings_path).get("theme") or self.cfg["default_theme"]
        try:
            theme = themes.load_theme(wanted, self.theme_dirs)
            fallback = None
        except themes.ThemeError as e:
            self.log.error("can't use theme: %s", e)
            theme = themes.load_theme(self.cfg["fallback_theme"], self.theme_dirs)
            fallback = {"wanted": wanted, "problems": e.problems}
        for w in theme.warnings:
            self.log.warning("theme %s: %s", theme.id, w)
        return theme, fallback

    async def index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(ENGINE_DIR / "index.html")

    async def api_theme(self, request: web.Request) -> web.Response:
        theme, fallback = self.active_theme()
        # Start optimizing the background video as soon as a theme is used,
        # so it's ready by the next load if it isn't already.
        bg = theme.manifest.get("background") or {}
        if bg.get("type") == "video" and (bg_path := themes.safe_path(theme.root, bg.get("src"))):
            if bg_path.is_file() and self.videos.is_video(bg_path):
                self.videos.served_path(bg_path)
        settings = owner_settings(read_settings(self.settings_path))
        folders = [("owner", Path(self.cfg["owner_layouts_dir"]) / theme.id)]
        style = read_settings(Path(self.cfg["owner_styles_dir"]) / theme.id / "custom.json") or None
        return web.json_response(theme_payload(theme, fallback, settings, folders, self.log, style,
                                               [Path(d) for d in self.cfg["module_dirs"]]))

    async def module_file(self, request: web.Request) -> web.StreamResponse:
        return module_file_response([Path(d) for d in self.cfg["module_dirs"]],
                                    request.match_info["module"], request.match_info["path"])

    async def media_file(self, request: web.Request) -> web.StreamResponse:
        path = media_path(Path(self.cfg["media_dir"]), request.match_info["name"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def theme_file(self, request: web.Request) -> web.StreamResponse:
        root = themes.find_theme_dir(request.match_info["theme_id"], self.theme_dirs)
        if root is None:
            raise web.HTTPNotFound()
        path = themes.safe_path(root, request.match_info["path"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        if self.videos.is_video(path):
            path = self.videos.served_path(path)
        return web.FileResponse(path)


def module_file_response(folders: list[Path], module_id: str, rel: str) -> web.StreamResponse:
    """A module's style sheet (wrapped in @scope) or picture, for its blocks."""
    found = module_blocks.asset(folders, module_id, rel)
    if found is None:
        raise web.HTTPNotFound()
    body, kind = found
    if isinstance(body, Path):
        return web.FileResponse(body)
    return web.Response(body=body, content_type=kind)


@web.middleware
async def headers(request: web.Request, handler):
    response = await handler(request)
    # The legacy page is one big inline script; it predates the CSP.
    if not request.path.startswith("/legacy/"):
        response.headers["Content-Security-Policy"] = CSP
    # Theme and engine edits should show on the next reload, not after a
    # cache expires.
    response.headers["Cache-Control"] = "no-cache"
    return response


def build_app(cfg: dict, log) -> web.Application:
    app_state = App(cfg, log)
    app = web.Application(middlewares=[headers])
    app.router.add_get("/", app_state.index)
    app.router.add_get("/api/theme", app_state.api_theme)
    app.router.add_get("/themes/{theme_id}/{path:.+}", app_state.theme_file)
    app.router.add_get("/media/{name}", app_state.media_file)
    app.router.add_get("/modules/{module}/{path:.+}", app_state.module_file)
    app.router.add_static("/engine/", ENGINE_DIR)
    app.router.add_static("/legacy/", LEGACY_DIR)
    return app


def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = {**DEFAULTS, **config.load(SERVICE_NAME)}
    log.info("serving on http://%s:%s, themes from %s", cfg["http_host"], cfg["http_port"], cfg["theme_dirs"])
    web.run_app(build_app(cfg, log), host=cfg["http_host"], port=cfg["http_port"], print=None)


if __name__ == "__main__":
    main()
