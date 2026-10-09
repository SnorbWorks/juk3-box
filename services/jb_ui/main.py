"""jb-ui: serves the kiosk's UI engine and the active theme.

Listens on the Pi itself only (127.0.0.1): the kiosk browser is the only
client. Phone/laptop admin is jb-admin's job, on its own port.

Routes:
  /                    the engine page
  /engine/...          engine scripts and styles
  /api/theme           the active theme: manifest, layouts and block
                       markup, validated and cleaned
  /themes/<id>/...     files from that theme's folder
  /legacy/...          the first-build song board, kept until the engine
                       can do everything it did
"""

import json
from pathlib import Path

from aiohttp import web

from shared import config, logging as jb_logging
from services.jb_ui import markup, themes

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
NAV_BUTTONS = ("home", "volume", "shuffle", "all-songs", "settings")
DEFAULT_NAV = ["home", "volume", "shuffle", "all-songs", "settings"]

# Home sections and Browse cards the engine knows (docs/theme-format.md,
# "Home sections").
HOME_SECTIONS = ("new", "featured-artist", "browse", "recents")
DEFAULT_HOME_SECTIONS = ["new", "featured-artist", "browse", "recents"]
BROWSE_SORTS = ("title", "artist", "album", "genre", "top-100", "most-played")
DEFAULT_BROWSE = ["artist", "album", "top-100", "most-played"]


def _choice_list(value, known, default):
    """An owner's list of choices, unknown ones and repeats dropped. An empty
    list is a real choice (show none); anything not a list is the default."""
    if not isinstance(value, list):
        return default
    return list(dict.fromkeys(v for v in value if v in known))


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
        "home_sections": _choice_list(raw.get("home_sections"), HOME_SECTIONS, DEFAULT_HOME_SECTIONS),
        "browse": _choice_list(raw.get("browse"), BROWSE_SORTS, DEFAULT_BROWSE),
    }


class App:
    def __init__(self, cfg: dict, log):
        self.cfg = cfg
        self.log = log
        self.theme_dirs = [Path(d) for d in cfg["theme_dirs"]]
        self.settings_path = Path(cfg["settings_path"])

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
        settings = owner_settings(read_settings(self.settings_path))
        base = f"/themes/{theme.id}/"
        return web.json_response({
            "id": theme.id,
            "base": base,
            "manifest": theme.manifest,
            "layouts": theme.layouts,
            "blocks": markup.load_blocks(ENGINE_DIR / "blocks", theme.root, base),
            "icons": {name: base + path for name, path in themes.icons(theme.root).items()},
            "browse_images": {name: base + path for name, path in themes.browse_images(theme.root).items()},
            "settings": settings,
            "warnings": theme.warnings,
            "fallback": fallback,
        })

    async def theme_file(self, request: web.Request) -> web.StreamResponse:
        root = themes.find_theme_dir(request.match_info["theme_id"], self.theme_dirs)
        if root is None:
            raise web.HTTPNotFound()
        path = themes.safe_path(root, request.match_info["path"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)


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
