"""The helper app as a source: its WebSocket sessions, pairing, the
library it sends, and jb-core's picks (docs/contracts/helper-protocol.md).

On the bus it looks like the Bluetooth phone (jukebox/jb-source-helper/
connected, sync, now-playing, play-error), so jb-core, the screen and the
lights treat it the same way; its songs' item_path is
"helper:<device>/<item>". One phone is the active one, the one jb-core
chose (jukebox/jb-core/source: the cabinet's Sources button), or none while
a Bluetooth phone has the jukebox. Every paired phone's own state is in
connected's "phones", for that choice.
"""

import asyncio
import json
import time
from pathlib import Path

from shared.files import write_atomic
from services.jb_source_helper import protocol
from services.jb_source_helper.phones import Challenge, Phones

TOPIC = "jukebox/jb-source-helper"
HELLO_WAIT_S = 10.0
ANSWER_WAIT_S = 15.0       # an ack from the app (MusicKit can be slow to start a song)
WAKE_WAIT_S = 20.0         # a sleeping app, woken over Bluetooth LE, coming back
LIBRARY_WAIT_S = 300.0     # a big library over Wi-Fi
# Paired and seen this recently: picks are sent (waking the app if needed)
# rather than refused. After that it counts as gone (left the house).
REACHABLE_S = 12 * 3600
ARTWORK_SIZE = 600
# Codes in a row before the cabinet stops asking (an app left open on the
# pairing screen, or one nobody is answering): about 6 minutes.
PAIR_ROUNDS = 3
SETTINGS = {"allow_pairing": None, "ble": True, "name": None}   # None: see Helper.settings
PAIR_CLOSED = "Pairing is off on {name}. Turn it on in the jukebox admin: Sources > App."


class CommandError(Exception):
    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


class Session:
    """One connected app."""

    def __init__(self, ws, device_id: str, name: str):
        self.ws, self.device_id, self.name = ws, device_id, name
        self.pending: dict[str, asyncio.Future] = {}
        self.chunks: list = []

    async def send(self, type_: str, payload: dict | None = None, id_: str | None = None) -> None:
        await self.ws.send_str(protocol.message(type_, payload, id_))


class Helper:
    def __init__(self, bus, phones: Phones, data_dir: Path, log, jukebox_id: str, jukebox_name: str, ble=None):
        self.bus, self.phones, self.log = bus, phones, log
        self.data_dir = data_dir
        self.art_dir = data_dir / "artwork"
        self.jukebox_id, self.jukebox_name = jukebox_id, jukebox_name
        self.ble = ble                          # ble.Wake, or None
        self.sessions: dict[str, Session] = {}
        self.active: str | None = None          # the phone source's device id
        self.challenge: Challenge | None = None
        self.music: dict = {}                   # device id -> its last music_status
        self.playing: dict | None = None        # the last playback_state, for now-playing
        self.came_back: dict[str, asyncio.Event] = {}
        self.sync_state = "none"
        self.failed_recently: dict[str, float] = {}
        self.chosen: str | None = None          # jb-core's choice, a device id, until it's heard
        self.on_rename = None                   # main.py: announce the new name
        self._settings = {**SETTINGS, **self._read("settings.json")}
        if self._settings["name"]:
            self.jukebox_name = self._settings["name"]

    # -- files -----------------------------------------------------------

    def _read(self, name: str) -> dict:
        try:
            return json.loads((self.data_dir / name).read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, name: str, data) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.data_dir / name, json.dumps(data))

    def library_of(self, device_id: str) -> dict:
        return self._read(f"library-{device_id}.json")

    def settings(self) -> dict:
        """The admin's Sources > App switches. New phones may pair until one
        has (then it's off until the owner turns it on again)."""
        s = dict(self._settings)
        if s["allow_pairing"] is None:
            s["allow_pairing"] = not self.phones.phones
        s["name"] = self.jukebox_name
        return s

    def _save_settings(self, **changes) -> None:
        self._settings.update(changes)
        self._write("settings.json", self._settings)

    async def ble_update(self) -> None:
        if self.ble:
            await self.ble.ensure_running(bool(self.phones.phones) and self.settings()["ble"])

    # -- the bus -----------------------------------------------------------

    def reachable(self, device_id: str | None) -> bool:
        if not device_id or device_id not in self.phones.phones:
            return False
        if device_id in self.sessions:
            return True
        seen = self.phones.phones[device_id].get("last_seen") or 0
        return self.ble is not None and self.ble.running and time.time() - seen < REACHABLE_S

    async def publish_state(self) -> None:
        d = self.active
        name = self.phones.name_of(d) if d else None
        await self.bus.publish(f"{TOPIC}/connected", {
            "connected": self.reachable(d), "name": name, "device_id": d,
            "awake": d in self.sessions, "music": self.music.get(d) or None,
            "phones": [{**p, "reachable": self.reachable(p["device_id"]), "awake": p["device_id"] in self.sessions,
                        "songs": len(self.library_of(p["device_id"]).get("tracks") or []),
                        "synced_at": self.library_of(p["device_id"]).get("at")}
                       for p in self.phones.listing()],
        }, retain=True)
        await self.bus.publish(f"{TOPIC}/sync", {"state": self.sync_state, "name": name}, retain=True)
        await self.bus.publish(f"{TOPIC}/settings", self.settings(), retain=True)

    async def publish_library(self) -> None:
        d = self.active
        lib = self.library_of(d) if d else {}
        await self.bus.publish(f"{TOPIC}/library", {
            "v": 1, "device_id": d, "name": self.phones.name_of(d) if d else None, "hash": lib.get("hash"),
            "tracks": lib.get("tracks") or [], "playlists": lib.get("playlists") or [],
        }, retain=True)

    async def publish_pairing(self) -> None:
        c = self.challenge
        await self.bus.publish(f"{TOPIC}/pairing", {"state": "code", "code": c.code, "device": c.name,
                                                    "expires": c.expires} if c else {"state": "none"},
                               retain=True)

    async def start(self) -> None:
        if self.sync_state == "none" and self.active and self.library_of(self.active):
            self.sync_state = "ready"           # the last library, until the app sends a new one
        await self.publish_state()
        await self.publish_library()
        await self.publish_pairing()
        await self.bus.publish(f"{TOPIC}/now-playing", protocol.now_playing(self.active, None))

    # -- a connection ------------------------------------------------------

    async def serve(self, ws) -> None:
        """Runs one app connection, from hello to close (main.py's /helper)."""
        await ws.send_str(protocol.message("hello", {
            "jukeboxName": self.jukebox_name, "jukeboxId": self.jukebox_id, "protocolVersions": [protocol.VERSION],
            "ble": self.ble.uuids() if self.ble else None}))
        hello = await self._receive(ws, HELLO_WAIT_S)
        if not hello or hello["type"] != "client_hello":
            return
        p = hello["payload"]
        device_id, name = str(p.get("deviceId") or ""), str(p.get("deviceName") or "iPhone")[:60]
        if not device_id or len(device_id) > 64:
            return
        if not self.phones.known(device_id, p.get("token")):
            if not self.settings()["allow_pairing"]:
                self.log.info("%s wants to pair, but pairing is off", name)
                await ws.send_str(protocol.message("pair_closed", {"message": PAIR_CLOSED.format(name=self.jukebox_name)}))
                return
            if not await self._pair(ws, device_id, name, p.get("model")):
                return
            self._save_settings(allow_pairing=False)    # the next phone needs the owner's say-so
        session = Session(ws, device_id, name)
        old = self.sessions.get(device_id)
        if old:
            await old.ws.close()
        self.sessions[device_id] = session
        self.phones.seen(device_id, name)
        await self.ble_update()
        # Not the source unless jb-core chose it: a phone arriving while
        # another has the jukebox is offered on the cabinet instead.
        await session.send("welcome", {"deviceId": device_id, "active": device_id == self.active})
        self.came_back.setdefault(device_id, asyncio.Event()).set()
        self.log.info("%s connected%s", name, "" if device_id == self.active else " (not the source)")
        await self.publish_state()
        asyncio.create_task(self.sync_library(device_id))
        try:
            async for msg in ws:
                if msg.type != 1:               # aiohttp WSMsgType.TEXT
                    continue
                m = protocol.parse(msg.data)
                if m:
                    await self.heard(session, m)
        finally:
            if self.sessions.get(device_id) is session:
                del self.sessions[device_id]
                self.came_back[device_id] = asyncio.Event()
            for f in session.pending.values():
                if not f.done():
                    f.set_exception(CommandError("no_answer", "the app went away"))
            self.phones.seen(device_id)
            self.log.info("%s disconnected", name)
            await self.publish_state()

    async def _receive(self, ws, timeout: float) -> dict | None:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout)
        except asyncio.TimeoutError:
            return None
        return protocol.parse(msg.data) if msg.type == 1 else None

    async def _pair(self, ws, device_id: str, name: str, model) -> bool:
        """The cabinet shows a code; the app sends it back. True once paired."""
        self.challenge = Challenge(device_id, name)
        rounds = 1
        await self.publish_pairing()
        self.log.info("pairing %s: code on the screen", name)
        try:
            await ws.send_str(protocol.message("pair_challenge", {"digits": 4, "expires": int(self.challenge.expires - time.time())}))
            while True:
                left = self.challenge.expires - time.time()
                m = await self._receive(ws, max(1.0, left))
                if m is None:
                    if ws.closed or rounds >= PAIR_ROUNDS:
                        self.log.info("pairing %s: no answer, stopped asking", name)
                        return False
                    rounds += 1
                    self.challenge.renew()      # ran out: a new code
                    await self.publish_pairing()
                    await ws.send_str(protocol.message("pair_challenge", {"digits": 4, "expires": int(self.challenge.expires - time.time())}))
                    continue
                if m["type"] != "pair_response":
                    continue
                if self.challenge.check(m["payload"].get("code")):
                    token = self.phones.add(device_id, name, model if isinstance(model, str) else None)
                    await ws.send_str(protocol.message("paired", {"token": token}))
                    self.log.info("paired %s", name)
                    return True
                if self.challenge.tries > 0:
                    await ws.send_str(protocol.message("pair_failed", {"triesLeft": self.challenge.tries}))
                elif rounds >= PAIR_ROUNDS:
                    await ws.send_str(protocol.message("pair_failed", {"triesLeft": 0}))
                    self.log.info("pairing %s: too many wrong codes, stopped asking", name)
                    return False
                else:
                    rounds += 1
                    self.challenge.renew()
                    await self.publish_pairing()
                    await ws.send_str(protocol.message("pair_failed", {"triesLeft": 0}))
                    await ws.send_str(protocol.message("pair_challenge", {"digits": 4, "expires": int(self.challenge.expires - time.time())}))
        finally:
            self.challenge = None
            await self.publish_pairing()

    async def _send_quietly(self, session: Session, type_: str, payload: dict) -> None:
        try:
            await self._command(session, type_, payload)
        except CommandError:
            pass

    # -- what the app says -------------------------------------------------

    async def heard(self, session: Session, m: dict) -> None:
        t, p = m["type"], m["payload"]
        if t in ("ack", "error"):
            f = session.pending.pop(str(m.get("id")), None)
            if f and not f.done():
                if t == "ack":
                    f.set_result(p)
                else:
                    f.set_exception(CommandError(str(p.get("reason") or "other"), str(p.get("message") or "")))
        elif t == "library_chunk":
            items = p.get("items")
            if isinstance(items, list):
                session.chunks.append((p.get("kind"), items))
        elif t == "library_done":
            await self._library_done(session, p)
        elif t == "playback_state":
            if session.device_id == self.active:
                self.playing = p
                await self.bus.publish(f"{TOPIC}/now-playing", protocol.now_playing(session.device_id, p))
        elif t == "track_ended":
            if session.device_id == self.active:
                ended = {**protocol.now_playing(session.device_id, None), "ended": True,
                         "item_path": protocol.item_path(session.device_id, str(p.get("itemId") or ""))}
                self.playing = None
                await self.bus.publish(f"{TOPIC}/now-playing", ended)
        elif t == "play_failed":
            await self._play_failed(session.device_id, str(p.get("itemId") or ""), str(p.get("reason") or "other"))
        elif t == "music_status":
            self.music[session.device_id] = {k: p.get(k) for k in ("authorization", "canPlay", "route")}
            await self.publish_state()
        else:
            self.log.debug("ignoring %s from %s", t, session.name)

    async def _play_failed(self, device_id: str, item_id: str, reason: str, title: str | None = None) -> None:
        path = protocol.item_path(device_id, item_id)
        now = time.monotonic()
        if now - self.failed_recently.get(path, -10) < 5:    # said once already (the error and the event)
            return
        self.failed_recently[path] = now
        self.log.warning("%s didn't play: %s", path, reason)
        await self.bus.publish(f"{TOPIC}/play-error", {"item_path": path, "title": title, "reason": reason})

    # -- library -------------------------------------------------------------

    async def sync_library(self, device_id: str, full: bool = False) -> None:
        session = self.sessions.get(device_id)
        if not session or device_id != self.active:
            return
        session.chunks = []
        lib = self.library_of(device_id)
        self.sync_state = "syncing" if not lib else "ready"   # a known library plays on while it's checked
        await self.publish_state()
        since = None if full else lib.get("hash")
        try:
            await self._command(session, "request_library", {"since": since} if since else {},
                                timeout=LIBRARY_WAIT_S)
        except CommandError as e:
            self.log.warning("library from %s: %s", session.name, e.reason)
            if not lib:
                self.sync_state = "failed"
                await self.publish_state()

    async def _library_done(self, session: Session, p: dict) -> None:
        device_id = session.device_id
        lib = self.library_of(device_id)
        if not session.chunks and p.get("hash") and p.get("hash") == lib.get("hash"):
            self.log.info("library from %s unchanged", session.name)
        else:
            songs = [i for kind, items in session.chunks if kind == "song" for i in items]
            lists = [i for kind, items in session.chunks if kind == "playlist" for i in items]
            tracks = [t for t in (protocol.track(device_id, s) for s in songs) if t]
            lib = {"hash": p.get("hash"), "tracks": tracks, "playlists": protocol.playlists(lists, songs),
                   "at": time.time()}
            self._write(f"library-{device_id}.json", lib)
            self.log.info("library from %s: %d songs, %d playlists", session.name, len(tracks), len(lib["playlists"]))
        session.chunks = []
        if device_id == self.active:
            self.sync_state = "ready"
            await self.publish_library()
            await self.publish_state()
        asyncio.create_task(self.fetch_artwork(device_id))

    async def fetch_artwork(self, device_id: str) -> None:
        keys = sorted({t["artwork_key"] for t in self.library_of(device_id).get("tracks") or [] if t.get("artwork_key")})
        missing = [k for k in keys if not (self.art_dir / f"{k}.jpg").exists()]
        session = self.sessions.get(device_id)
        if not missing or not session:
            return
        for i in range(0, len(missing), 200):
            try:
                await self._command(session, "request_artwork", {"keys": missing[i:i + 200], "size": ARTWORK_SIZE},
                                    timeout=LIBRARY_WAIT_S)
            except CommandError as e:
                self.log.info("artwork from %s: %s", session.name, e.reason)
                return

    def save_artwork(self, key: str, body: bytes) -> None:
        self.art_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.art_dir / f"{key}.jpg", body)

    # -- jb-core's commands --------------------------------------------------

    async def _command(self, session: Session, type_: str, payload: dict, timeout: float = ANSWER_WAIT_S) -> dict:
        id_ = protocol.new_id()
        f = asyncio.get_running_loop().create_future()
        session.pending[id_] = f
        try:
            await session.send(type_, payload, id_)
            return await asyncio.wait_for(f, timeout)
        except asyncio.TimeoutError:
            raise CommandError("no_answer", f"no answer to {type_}")
        except ConnectionError:
            raise CommandError("no_answer", "the app went away")
        finally:
            session.pending.pop(id_, None)

    async def session_for(self, device_id: str) -> Session:
        """The app's connection, waking it over Bluetooth LE if it's asleep."""
        s = self.sessions.get(device_id)
        if s:
            return s
        if not self.ble or not self.ble.running:
            raise CommandError("no_answer", "the app isn't connected")
        event = self.came_back.setdefault(device_id, asyncio.Event())
        event.clear()
        self.log.info("waking %s", device_id)
        await self.ble.wake()
        try:
            await asyncio.wait_for(event.wait(), WAKE_WAIT_S)
        except asyncio.TimeoutError:
            raise CommandError("no_answer", "the app didn't wake up")
        s = self.sessions.get(device_id)
        if not s:
            raise CommandError("no_answer", "the app didn't wake up")
        return s

    async def command(self, cmd: dict) -> None:
        action = cmd.get("action")
        if action == "play_item":
            await self.play(cmd.get("item_path"), cmd.get("title"))
        elif action in ("pause", "stop", "play"):
            if not self.active:
                return
            try:
                s = await self.session_for(self.active)
                await self._command(s, {"play": "resume"}.get(action, action), {})
            except CommandError as e:
                self.log.info("%s: %s", action, e.reason)
            if action == "stop":
                self.playing = None
                await self.bus.publish(f"{TOPIC}/now-playing", protocol.now_playing(self.active, None))
        elif action == "forget":
            device_id = cmd.get("device_id")
            if isinstance(device_id, str) and self.phones.forget(device_id):
                s = self.sessions.pop(device_id, None)
                if s:
                    await s.ws.close()
                (self.data_dir / f"library-{device_id}.json").unlink(missing_ok=True)
                if self.active == device_id:
                    self.active = None
                    self.sync_state = "none"
                    await self.publish_library()
                await self.bus.publish(f"{TOPIC}/forgotten", {"mac": protocol.PREFIX + device_id})
                await self.ble_update()
                await self.publish_state()
                self.log.info("forgot %s", device_id)
        elif action == "rename":
            device_id, name = cmd.get("device_id"), clean_name(cmd.get("name"))
            if device_id in self.phones.phones and name:
                self.phones.rename(device_id, name)
                await self.publish_state()
                if device_id == self.active:
                    await self.publish_library()
                self.log.info("renamed %s to %s", device_id, name)
        elif action == "sync":
            if self.active:
                self.log.info("syncing %s's library again, in full", self.phones.name_of(self.active))
                await self.sync_library(self.active, full=True)
        elif action == "settings":
            changes = {k: bool(cmd[k]) for k in ("allow_pairing", "ble") if k in cmd}
            name = clean_name(cmd.get("name")) if "name" in cmd else None
            if name and name != self.jukebox_name:
                changes["name"] = self.jukebox_name = name
                self._write_airplay_name(name)
                if self.on_rename:
                    await self.on_rename(name)
            if changes:
                self._save_settings(**changes)
                self.log.info("settings: %s", changes)
                await self.ble_update()
            await self.publish_state()

    def _write_airplay_name(self, name: str) -> None:
        """jb-airplay's speaker name (its unit reads this file; jb-admin
        restarts it), so the AirPlay picker and the app agree."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.data_dir / "airplay.env", f'JB_AIRPLAY_NAME="{name}"\n')

    async def follow_choice(self, source: dict) -> None:
        """jukebox/jb-core/source: the phone jb-core chose. One of ours, or
        none (a Bluetooth phone has the jukebox)."""
        active = (source or {}).get("active") or {}
        want = None
        if active.get("kind") == "helper" and str(active.get("id", "")).startswith(protocol.PREFIX):
            want = active["id"][len(protocol.PREFIX):]
            if want not in self.phones.phones:
                want = None
        if want == self.active:
            return
        self.active = want
        lib = self.library_of(want) if want else {}
        self.sync_state = "ready" if lib else ("syncing" if want in self.sessions else "none")
        self.playing = None
        self.log.info("the source is now %s", self.phones.name_of(want) if want else "not the app")
        for d, s in self.sessions.items():
            asyncio.create_task(self._send_quietly(s, "set_active", {"active": d == want}))
        await self.publish_state()
        await self.publish_library()
        if want:
            asyncio.create_task(self.sync_library(want))

    async def play(self, path, title) -> None:
        split = protocol.split_item_path(path)
        if not split:
            await self.bus.publish(f"{TOPIC}/play-error", {"item_path": path, "title": title, "reason": "not_found"})
            return
        device_id, item_id = split
        try:
            s = await self.session_for(device_id)
            self.log.info("playing %s", path)
            await self._command(s, "play", {"itemId": item_id, "kind": "song"})
        except CommandError as e:
            await self._play_failed(device_id, item_id, e.reason, title)


def clean_name(name) -> str | None:
    """A name typed in the admin: shown on the cabinet, announced over
    Bonjour and AirPlay (and written into airplay.env, so no quotes)."""
    if not isinstance(name, str):
        return None
    name = " ".join("".join(c for c in name if c.isprintable() and c not in '"\\$`').split())[:40]
    return name or None
