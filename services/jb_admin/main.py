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
from services.jb_admin.extensions import Extensions
from services.jb_admin.files import Layouts, read_json, write_bytes, write_json
from services.jb_admin.media_lib import MediaLibrary
from services.jb_admin.drive_import import DriveImport
from services.jb_admin.module_mgr import Manager as ModuleManager, ModuleError, review as module_review, unit_of
from services.jb_admin.registry import Registry, read_manifests
from services.jb_ui import main as ui
from services.jb_ui import customize, icons, media, module_blocks, themes

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
    "module_settings_dir": str(DATA / "module-settings"),
    "modctl_dir": str(DATA / "modctl"),
    "default_theme": "bare",
    "max_upload_mb": 600,
    # Pictures and videos imported from USB drives stop once the media
    # library is this big (drive_import.py); music is never copied.
    "drive_media_cap_mb": 2048,
}

COOKIE = "jbadmin"

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
        self.media = MediaLibrary(self.media_dir, log)
        self.drive_import = DriveImport(self.media, Path(cfg["admin_dir"]) / "drive-imports.json", log,
                                        cfg["drive_media_cap_mb"], cfg["max_upload_mb"])
        self._import_task = None
        self._offers_sent = None
        self.auth = Auth(Path(cfg["admin_dir"]))
        self.extensions = Extensions(REPO_DIR / "deploy" / "extensions.json")
        self.registry = Registry([Path(d) for d in cfg["module_dirs"]])
        dirs = [Path(d) for d in cfg["module_dirs"]]
        self.modules = ModuleManager(
            dirs[-1], Path(cfg["module_settings_dir"]), Path(cfg["admin_dir"]) / "module-secrets",
            Path(cfg["modctl_dir"]), lambda: set(read_manifests(dirs[:1])), self.registry, log)
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
        "jukebox/jb-core/pick-log": "pick_log",
        "jukebox/jb-core/queue": "queue",
        "jukebox/jb-source-phone/phones": "phones",
        "jukebox/jb-source-phone/pairing": "pairing",
        "jukebox/jb-source-phone/switch": "phone_switch",
        "jukebox/jb-source-phone/bluetooth": "bluetooth",
        "jukebox/jb-audio/airplay": "airplay",
        "jukebox/jb-display/state": "display",
        "jukebox/jb-source-helper/connected": "helper",
        "jukebox/jb-source-helper/settings": "helper_settings",
        "jukebox/jb-core/source": "source",
        "jukebox/jb-library/drives-in": "drives_in",
    }

    # What the Phones page may ask jb-source-phone to do.
    PHONE_ACTIONS = {"bluetooth", "pair_start", "pair_stop", "pair_confirm", "switch", "disconnect", "forget"}

    LIBRARY_TOPICS = ("jukebox/jb-library/tracks", "jukebox/jb-library/playlists")
    DRIVES_TOPIC = "jukebox/jb-source-local/library"
    # Drives with new pictures or videos, for the cabinet to ask "Import
    # them?" (retained), and the cabinet's answers.
    DRIVE_OFFERS_TOPIC = "jukebox/jb-admin/drive-offers"
    CMD_TOPIC = "jukebox/jb-admin/cmd"

    async def publish_offers(self) -> None:
        """The drive offers, when they changed (or on a new connection)."""
        offers = self.drive_import.offers()
        if self.bus is None or offers == self._offers_sent:
            return
        await self.bus.publish(self.DRIVE_OFFERS_TOPIC, {"offers": offers}, retain=True)
        self._offers_sent = offers

    async def heard_cmd(self, payload) -> None:
        """The cabinet's answer to "Import them?": {"action": "import_drive"
        or "decline_drive", "id": drive id}."""
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            return
        if payload.get("action") == "import_drive":
            n = self.drive_import.import_drive(payload["id"])
            self.log.info("drives: importing %d files from %s (asked at the cabinet)", n, payload["id"])
        elif payload.get("action") == "decline_drive":
            self.drive_import.decline(payload["id"])
            self.log.info("drives: not importing from %s for now", payload["id"])
        await self.publish_offers()

    def heard_library(self, topic: str, payload) -> None:
        """Keeps only the names out of jb-library's snapshots."""
        if not isinstance(payload, dict):
            return
        if topic.endswith("/tracks"):
            # Names only: the full snapshot is big, and the Pi short of memory.
            self._library_tracks = [{k: t.get(k) for k in ("artist", "album", "genre")}
                                    for t in payload.get("tracks") or [] if isinstance(t, dict)]
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
                    self._offers_sent = None
                    self.log.info("connected to the bus")
                    heartbeat = asyncio.create_task(bus.run_heartbeat())
                    try:
                        for topic in ("jukebox/+/heartbeat", "jukebox/+/status", "jukebox/analysis/+/status",
                                      self.DRIVES_TOPIC, "jukebox/jb-library/drives", self.CMD_TOPIC,
                                      *self.LIVE_TOPICS, *self.LIBRARY_TOPICS):
                            await bus._client.subscribe(topic)
                        messages = bus._iter_messages()
                        async for topic, payload in messages:
                            if topic in self.LIVE_TOPICS:
                                self.live[self.LIVE_TOPICS[topic]] = payload
                            elif topic in self.LIBRARY_TOPICS:
                                self.heard_library(topic, payload)
                            elif topic == self.DRIVES_TOPIC:
                                self.live["drives_local"] = payload
                                self.drive_import.heard(payload)
                                await self.publish_offers()
                            elif topic == self.CMD_TOPIC:
                                await self.heard_cmd(payload)
                            elif topic == "jukebox/jb-library/drives":
                                self.live["drives_library"] = payload
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
        """Each module's unit state, by module id (installed modules run as
        jbmod-<id>)."""
        mods = self.registry.view()
        by_unit = {unit_of(m): m["id"] for m in mods}
        units = list(by_unit)
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
                states[by_unit.get(unit, unit)] = line[len("ActiveState="):]
        return states

    async def status(self, request):
        return web.json_response({
            "modules": self.registry.view(await self.systemd_states()),
            "live": self.live,
            "bus": self.bus is not None,
            "theme": self.active_theme(),
            "theme_name": self.theme_name(self.active_theme()),
        })

    # Extensions ---------------------------------------------------------------

    async def get_extensions(self, request):
        return web.json_response(await self.extensions.view())

    # Phones -------------------------------------------------------------------

    async def get_phones(self, request):
        return web.json_response({
            "phones": (self.live.get("phones") or {}).get("phones") or [],
            "pairing": self.live.get("pairing") or {"state": "idle"},
            "switch": self.live.get("phone_switch") or {"state": "idle"},
            "bluetooth": self.live.get("bluetooth") or {"on": True},
            "source": self.live.get("source") or {"active": None},
            "bus": self.bus is not None,
        })

    # Sources: which phone has the jukebox, and the app's settings ----------------

    async def get_sources(self, request):
        return web.json_response({
            "source": self.live.get("source") or {"active": None, "offer": None},
            "phones": (self.live.get("phones") or {}).get("phones") or [],
            "bluetooth": self.live.get("bluetooth") or {"on": True},
            "helper": self.live.get("helper") or {"phones": []},
            "helper_settings": self.live.get("helper_settings") or {},
            "drives": (self.live.get("drives_in") or {}).get("drives") or [],
            "bus": self.bus is not None,
        })

    async def use_source(self, request):
        """Give the jukebox to this phone (jb-core's phone_choice.py)."""
        pid = (await json_body(request)).get("id")
        if not isinstance(pid, str) or not (
                re.fullmatch(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", pid) or re.fullmatch(r"helper:[A-Za-z0-9-]{1,64}", pid)):
            raise error(400, "Which phone?")
        if self.bus is None:
            raise error(503, "The admin can't reach the jukebox right now. Try again in a moment.")
        await self.bus.publish("jukebox/jb-core/cmd", {"action": "use_source", "id": pid})
        self.log.info("sources: use %s", pid)
        return web.json_response({"ok": True})

    HELPER_ACTIONS = {"forget", "rename", "sync", "settings"}

    async def helper_action(self, request):
        """The app's phones and switches, carried out by jb-source-helper."""
        action = request.match_info["action"]
        if action not in self.HELPER_ACTIONS:
            raise web.HTTPNotFound()
        body = await json_body(request)
        cmd = {"action": action}
        if action in ("forget", "rename"):
            device_id = body.get("device_id")
            if not isinstance(device_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", device_id):
                raise error(400, "Which phone?")
            cmd["device_id"] = device_id
        name = None
        if action == "rename" or (action == "settings" and "name" in body):
            name = body.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
                raise error(400, "A name of 1 to 40 characters, please.")
            cmd["name"] = name.strip()
        if action == "settings":
            for k in ("allow_pairing", "ble"):
                if k in body:
                    cmd[k] = bool(body[k])
        if self.bus is None:
            raise error(503, "The admin can't reach the jukebox right now. Try again in a moment.")
        old_name = (self.live.get("helper_settings") or {}).get("name")
        await self.bus.publish("jukebox/jb-source-helper/cmd", cmd)
        self.log.info("helper app: %s %s", action, cmd.get("device_id", ""))
        if action == "settings" and name and name.strip() != old_name:
            # The AirPlay speaker takes the new name on a restart, once
            # jb-source-helper has written it (airplay.env).
            for _ in range(30):
                if (self.live.get("helper_settings") or {}).get("name") != old_name:
                    break
                await asyncio.sleep(0.1)
            try:
                await self.modules.ctl("unit", "restart", "jb-airplay")
            except ModuleError as e:
                self.log.warning("couldn't restart jb-airplay: %s", e)
                raise error(500, f"Renamed, but the AirPlay speaker keeps its old name until the jukebox restarts ({e}).")
        return web.json_response({"ok": True})

    async def phone_action(self, request):
        action = request.match_info["action"]
        if action not in self.PHONE_ACTIONS:
            raise web.HTTPNotFound()
        body = await json_body(request)
        cmd = {"action": action}
        if action in ("switch", "disconnect", "forget"):
            mac = body.get("mac")
            if not isinstance(mac, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", mac):
                raise error(400, "Which phone?")
            cmd["mac"] = mac.upper()
        if action == "pair_confirm":
            cmd["ok"] = bool(body.get("ok"))
        if action == "bluetooth":
            cmd["on"] = bool(body.get("on"))
        if self.bus is None:
            raise error(503, "The admin can't reach the jukebox right now. Try again in a moment.")
        await self.bus.publish("jukebox/jb-source-phone/cmd", cmd)
        self.log.info("phones: %s %s", action, cmd.get("mac", ""))
        return web.json_response({"ok": True})

    # Sound (whole-home audio) ----------------------------------------------------

    async def get_audio(self, request):
        state = self.live.get("volume") or {}
        return web.json_response({
            "volume": state.get("volume"),
            "whole_home": state.get("whole_home"),   # None until jb-audio reports it
            "receivers": (self.live.get("airplay") or {}).get("receivers") or [],
            "bus": self.bus is not None,
        })

    async def audio_action(self, request):
        """What the Sound page may ask jb-audio to do."""
        action = request.match_info["action"]
        body = await json_body(request)
        if action == "whole_home":
            cmd = {"action": "whole_home", "enabled": bool(body.get("enabled"))}
            airplay = body.get("airplay")
            if airplay is not None:
                if not isinstance(airplay, str) or not 0 < len(airplay.strip()) <= 100:
                    raise error(400, "Which AirPlay speaker?")
                cmd["airplay"] = airplay.strip()
        elif action == "whole_home_offset":
            ms = body.get("ms")
            if not isinstance(ms, (int, float)) or not -1000 <= ms <= 1000:
                raise error(400, "The correction is -1000 to 1000 milliseconds.")
            cmd = {"action": "whole_home_offset", "ms": int(ms)}
        elif action == "whole_home_resync":
            cmd = {"action": "whole_home_resync"}
        elif action == "airplay_match":
            value = body.get("value")
            if not isinstance(value, (int, float)) or not 0.25 <= value <= 3:
                raise error(400, "The match is 25% to 300% of the cabinet's level.")
            cmd = {"action": "airplay_match", "value": round(float(value), 2)}
        else:
            raise web.HTTPNotFound()
        if self.bus is None:
            raise error(503, "The admin can't reach the jukebox right now. Try again in a moment.")
        await self.bus.publish("jukebox/jb-audio/cmd", cmd)
        self.log.info("sound: %s", cmd)
        return web.json_response({"ok": True})

    # Displays -------------------------------------------------------------------

    DISPLAY_SIDES = ("top", "right", "bottom", "left")

    async def get_display(self, request):
        return web.json_response({
            "display": self.live.get("display"),   # None until jb-display reports it
            "bus": self.bus is not None,
        })

    async def display_action(self, request):
        """What the Displays page may ask jb-display to do. jb-display keeps
        each side within what the screen allows."""
        action = request.match_info["action"]
        body = await json_body(request)
        if action == "inset":
            inset = body.get("inset")
            if (not isinstance(inset, dict) or not inset or set(inset) - set(self.DISPLAY_SIDES)
                    or not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 2000 for v in inset.values())):
                raise error(400, "Each edge is a whole number of pixels, 0 or more.")
            cmd = {"action": "inset", "inset": inset}
        elif action == "inset_reset":
            cmd = {"action": "inset_reset"}
        elif action == "rotation":
            if body.get("degrees") not in (0, 90, 180, 270) or isinstance(body.get("degrees"), bool):
                raise error(400, "The rotation is 0, 90, 180 or 270 degrees.")
            cmd = {"action": "rotation", "degrees": body["degrees"]}
        elif action == "sleep":
            if body.get("minutes") not in (0, 5, 10, 15, 30, 60, 120) or isinstance(body.get("minutes"), bool):
                raise error(400, "Not one of the sleep choices.")
            cmd = {"action": "sleep", "minutes": body["minutes"]}
        elif action == "guide":
            cmd = {"action": "guide", "on": bool(body.get("on"))}
        elif action == "wake":
            cmd = {"action": "wake"}
        else:
            raise web.HTTPNotFound()
        if self.bus is None:
            raise error(503, "The admin can't reach the jukebox right now. Try again in a moment.")
        await self.bus.publish("jukebox/jb-display/cmd", cmd)
        self.log.info("display: %s", cmd)
        return web.json_response({"ok": True})

    # USB drives -----------------------------------------------------------------

    DRIVE_ACTIONS = {"rescan", "eject", "mount", "forget", "edit", "settings", "reimport", "import"}

    async def get_drives(self, request):
        """Everything the Drives page shows: each drive (plugged in, ejected or
        seen before) with its space, songs (as shown, edits included, with the
        file's own tags beside them), what was skipped, and the picture/video
        import; plus the import settings."""
        local = self.live.get("drives_local") or {}
        library = (self.live.get("drives_library") or {}).get("drives") or []
        tags = {t.get("item_path"): {k: t.get(k) for k in ("title", "artist", "album", "genre", "album_track")}
                for t in local.get("tracks") or [] if isinstance(t, dict)}
        mounted = {d.get("id"): d for d in local.get("drives") or [] if isinstance(d, dict)}
        ejected = {d.get("id"): d for d in local.get("ejected") or [] if isinstance(d, dict)}
        out, seen = [], set()
        for d in library:
            out.append(self._drive_view(d, mounted.get(d["id"]), ejected.get(d["id"]), tags))
            seen.add(d["id"])
        for drive_id, m in mounted.items():
            if drive_id not in seen:           # just plugged in: not in the library yet
                out.insert(0, self._drive_view({"id": drive_id, "label": m.get("label"), "attached": True,
                                                "songs": []}, m, None, tags))
        return web.json_response({"drives": out, "settings": self.drive_import.settings(),
                                  "bus": self.bus is not None})

    def _drive_view(self, d: dict, mounted: dict | None, ejected: dict | None, tags: dict) -> dict:
        view = {k: d.get(k) for k in ("id", "label", "last_seen", "song_count", "plays")}
        view["state"] = "plugged in" if mounted else ("ejected" if ejected else "not plugged in")
        if mounted:
            view["label"] = mounted.get("label") or view["label"]
            view.update({k: mounted.get(k) for k in ("fstype", "total", "used", "free", "scanning")})
            view["skipped"] = mounted.get("skipped") or []
            view["pictures"] = len(mounted.get("images") or [])
            view["videos"] = len(mounted.get("videos") or [])
            view["songs"] = [{**s, "tags": tags.get(s.get("item_path"))} for s in d.get("songs") or []]
            status = self.drive_import.status.get(d["id"])
            view["import"] = {**status, "importing": self.drive_import.importing(d["id"])} if status else None
        elif ejected:
            view["label"] = ejected.get("label") or view["label"]
        return view

    async def drive_action(self, request):
        action = request.match_info["action"]
        if action not in self.DRIVE_ACTIONS:
            raise web.HTTPNotFound()
        body = await json_body(request)
        if action == "settings":
            try:
                cap = int(body.get("cap_mb"))
            except (TypeError, ValueError):
                raise error(400, "The size limit should be a number of MB.")
            return web.json_response(self.drive_import.save_settings(cap))
        drive_id = body.get("id")
        if action in ("eject", "mount", "forget", "reimport", "import") and (
                not isinstance(drive_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", drive_id)):
            raise error(400, "Which drive?")
        if action == "reimport":
            if not self.drive_import.reimport(drive_id, str(body.get("path") or "")):
                raise error(400, "That file isn't on a drive that's plugged in.")
            return web.json_response({"ok": True})
        if action == "import":
            n = self.drive_import.import_drive(drive_id)
            if not n:
                raise error(400, "Nothing new to import from that drive.")
            self.log.info("drives: importing %d files from %s", n, drive_id)
            await self.publish_offers()
            return web.json_response({"ok": True, "queued": n})
        if self.bus is None:
            raise error(503, "The admin can't reach the jukebox right now. Try again in a moment.")
        if action in ("rescan", "eject", "mount"):
            await self.bus.publish("jukebox/jb-source-local/cmd", {"action": action, "id": drive_id})
        elif action == "forget":
            await self.bus.publish("jukebox/jb-library/cmd", {"action": "forget_drive", "id": drive_id})
        elif action == "edit":
            item_path = body.get("item_path")
            if not isinstance(item_path, str) or not item_path.startswith("drive:"):
                raise error(400, "Which song?")
            fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
            await self.bus.publish("jukebox/jb-library/cmd", {"action": "edit_drive_song", "item_path": item_path,
                                                              "fields": fields})
        self.log.info("drives: %s %s", action, drive_id or body.get("item_path") or "")
        return web.json_response({"ok": True})

    def theme_name(self, theme_id: str) -> str:
        try:
            return themes.load_theme(theme_id, self.theme_dirs).manifest.get("name") or theme_id
        except themes.ThemeError:
            return theme_id

    # Settings -----------------------------------------------------------------

    def nav_blocks(self) -> dict:
        """The module blocks that can go in the navigation bar, by id."""
        found, _ = module_blocks.module_blocks([Path(d) for d in self.cfg["module_dirs"]])
        return {name: b for name, b in found.items() if b.get("nav")}

    async def get_settings(self, request):
        return web.json_response({"values": schema.current(self.raw_settings()),
                                  "schema": schema.with_modules(self.nav_blocks())})

    async def put_settings(self, request):
        body = await json_body(request)
        clean, problems = schema.validate(body.get("values"), self.nav_blocks())
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
        """The engine's blocks plus the ones modules add (installed-modules.md)."""
        from services.jb_ui import module_blocks
        schema = json.loads(json.dumps(self.schema_blocks))
        found, _ = module_blocks.module_blocks([Path(d) for d in self.cfg["module_dirs"]])
        for name, b in found.items():
            settings = dict(b["settings"])
            if b["shapes"]:
                settings["shape"] = {"type": "enum", "label": "Layout", "default": "", "options":
                                     [{"value": "", "label": "Auto: fits its space"}]
                                     + [{"value": s[0], "label": s[0].replace("-", " ").capitalize()} for s in b["shapes"]]}
            schema["blocks"][name] = {"label": b["label"], "group": b["group"] if b["group"] in
                                      {g["id"] for g in schema["groups"]} else "content",
                                      "icon": "module", "description": b["description"] or f"From the {b['module']} module.",
                                      "module": b["module"], "kind": b["kind"], "settings": settings}
            if b["nav"]:
                schema["blocks"][name]["nav_managed"] = True
            # A playlist block can also be one of a Browse row's cards.
            if b["kind"] == "playlist":
                sorts = schema["blocks"]["section"]["settings"].get("sorts")
                if sorts:
                    sorts["options"].append({"value": name, "label": b["label"]})
        return web.json_response(schema)

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
        problems += schema.nav_placement_problems(tree, self.nav_blocks())
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
        problems += schema.nav_placement_problems(draft, self.nav_blocks())
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

    # Modules (docs/contracts/installed-modules.md) ---------------------------------------

    def module(self, mid: str) -> dict:
        found = next((m for m in self.registry.view() if m["id"] == mid and not m.get("undeclared")), None)
        if not found:
            raise web.HTTPNotFound()
        return found

    async def module_info(self, request):
        m = self.module(request.match_info["id"])
        states = await self.systemd_states()
        return web.json_response({**m, "unit": unit_of(m), "state": states.get(m["id"]),
                                  "review": module_review(m) if m.get("installed") else None,
                                  "settings": self.modules.get_settings(m["id"], m)})

    async def module_action(self, request):
        m = self.module(request.match_info["id"])
        action = request.match_info["action"]
        if action not in ("start", "stop", "restart"):
            raise web.HTTPNotFound()
        if m["id"] == SERVICE_NAME and action == "stop":
            raise error(400, "The admin can't turn itself off.")
        try:
            await self.modules.ctl("unit", action, unit_of(m))
        except ModuleError as e:
            raise error(400, str(e))
        return web.json_response({"ok": True})

    async def module_log(self, request):
        m = self.module(request.match_info["id"])
        return web.json_response({"lines": await self.modules.log_lines(unit_of(m))})

    async def put_module_settings(self, request):
        m = self.module(request.match_info["id"])
        body = await json_body(request)
        try:
            saved = self.modules.save_settings(m["id"], m, body.get("values"))
            # An installed module gets its secrets when it starts (systemd
            # credentials): a changed secret needs a restart to reach it.
            if self.modules.secrets_changed and m.get("installed"):
                await self.modules.ctl("unit", "restart", unit_of(m))
        except ModuleError as e:
            raise error(400, str(e))
        await self.announce(f"module:{m['id']}")
        return web.json_response(saved)

    async def geocode(self, request):
        """Places matching what the owner typed, for a location setting."""
        from services.jb_admin import places
        try:
            found = await places.find(request.query.get("q", ""))
        except Exception as e:     # offline, the service down
            self.log.warning("place search: %s", e)
            raise error(400, "Couldn't search for places right now. Is the jukebox online?")
        return web.json_response({"places": found})

    async def upload_module(self, request):
        tmp = await save_upload(request, Path(self.cfg["admin_dir"]) / "uploads", ".zip", 25 * 1024 * 1024)
        try:
            staged = await asyncio.to_thread(self.modules.stage, tmp)
        except ModuleError as e:
            raise error(400, str(e))
        finally:
            tmp.unlink(missing_ok=True)
        return web.json_response(staged)

    async def install_module(self, request):
        body = await json_body(request)
        try:
            done = await self.modules.install(str(body.get("token", "")))
        except ModuleError as e:
            raise error(400, str(e))
        await self.announce("modules")
        return web.json_response(done)

    async def discard_module(self, request):
        self.modules.discard(request.match_info["token"])
        return web.json_response({"ok": True})

    async def uninstall_module(self, request):
        m = self.module(request.match_info["id"])
        if not m.get("installed"):
            raise error(400, "The jukebox's own modules can't be removed.")
        try:
            await self.modules.uninstall(m["id"])
        except ModuleError as e:
            raise error(400, str(e))
        await self.announce("modules")
        return web.json_response({"ok": True})

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
        kinds = customize.IMAGE_SUFFIXES + (customize.VIDEO_SUFFIXES if request.query.get("video") == "1" else ())
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if p.is_file() and p.suffix.lower() in kinds \
                    and not rel.startswith("assets/fonts/") and themes.safe_path(root, rel):
                kind = "video" if p.suffix.lower() in customize.VIDEO_SUFFIXES else "image"
                found.append({"name": rel, "src": rel, "url": f"/themes/{theme_id}/{rel}", "kind": kind})
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
        return web.json_response({"media": self.media.items()})

    async def upload_media(self, request):
        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "file":
            raise error(400, "Choose a picture or video to upload.")
        self.media.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.media.folder / f".upload-{time.time_ns()}"
        limit = self.cfg["max_upload_mb"] * 1024 * 1024
        size = 0
        try:
            with open(tmp, "wb") as out:
                while chunk := await part.read_chunk(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise error(400, f"Too big: the limit is {self.cfg['max_upload_mb']} MB.")
                    out.write(chunk)
            item = await self.media.add(tmp, unquote(part.filename or "file"))
        except ValueError as e:
            raise error(400, str(e))
        finally:
            tmp.unlink(missing_ok=True)
        return web.json_response(item)

    async def delete_media(self, request):
        if not self.media.delete(request.match_info["name"]):
            raise web.HTTPNotFound()
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
        return web.json_response(ui.theme_payload(theme, None, settings, folders, self.log, style,
                                                  [Path(d) for d in self.cfg["module_dirs"]]))

    async def module_file(self, request):
        return ui.module_file_response([Path(d) for d in self.cfg["module_dirs"]],
                                       request.match_info["module"], request.match_info["path"])

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
           404: web.HTTPNotFound, 409: web.HTTPConflict, 429: web.HTTPTooManyRequests,
           503: web.HTTPServiceUnavailable}[status]
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
    r.add_get("/api/phones", admin.get_phones)
    r.add_get("/api/extensions", admin.get_extensions)
    r.add_post("/api/phones/{action}", admin.phone_action)
    r.add_get("/api/sources", admin.get_sources)
    r.add_post("/api/sources/use", admin.use_source)
    r.add_post("/api/helper/{action}", admin.helper_action)
    r.add_get("/api/audio", admin.get_audio)
    r.add_post("/api/audio/{action}", admin.audio_action)
    r.add_get("/api/display", admin.get_display)
    r.add_post("/api/display/{action}", admin.display_action)
    r.add_get("/api/drives", admin.get_drives)
    r.add_post("/api/drives/{action}", admin.drive_action)
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
    r.add_get("/api/places", admin.geocode)
    r.add_post("/api/modules/upload", admin.upload_module)
    r.add_post("/api/modules/install", admin.install_module)
    r.add_delete("/api/modules/staged/{token}", admin.discard_module)
    r.add_get("/api/modules/{id}", admin.module_info)
    r.add_post("/api/modules/{id}/{action}", admin.module_action)
    r.add_get("/api/modules/{id}/log", admin.module_log)
    r.add_put("/api/modules/{id}/settings", admin.put_module_settings)
    r.add_delete("/api/modules/{id}", admin.uninstall_module)
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
    r.add_get("/modules/{module}/{path:.+}", admin.module_file)
    r.add_static("/engine/", UI_DIR / "engine")

    async def start_bus(app):
        admin.media.resume()
        if bus_enabled:
            admin._bus_task = asyncio.create_task(admin.run_bus())
            admin._import_task = asyncio.create_task(admin.drive_import.run())

    async def stop_bus(app):
        if admin._bus_task:
            admin._bus_task.cancel()
        if admin._import_task:
            admin._import_task.cancel()

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
