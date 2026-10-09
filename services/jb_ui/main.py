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
    # Owner choices (active theme, later Home sections, nav buttons, ...).
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
        base = f"/themes/{theme.id}/"
        return web.json_response({
            "id": theme.id,
            "base": base,
            "manifest": theme.manifest,
            "layouts": theme.layouts,
            "blocks": markup.load_blocks(ENGINE_DIR / "blocks", theme.root, base),
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
