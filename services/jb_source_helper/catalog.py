"""Apple Music itself, through the helper app (docs/contracts/apple-music.md).

The jukebox has no Apple account: its genres, charts and chart lists come
from the owner's phone (the app's catalog_* commands, MusicKit and the
owner's subscription). What the app sends is kept on disk, one file per
topic under <data_dir>/catalog/, for refresh_hours, and published retained
on jukebox/jb-source-helper/catalog/... with the owner's filters (clean,
genres) applied each time, so changing them needs no new fetch. Covers are
Apple's public image links: downloaded here, into the folder the app's own
uploads go to, so jb-ui serves them at /covers/<sha1 of the link>.jpg.

The phone's side is in source.py (Helper): this module asks it through
Helper.catalog_command and reads Helper's state for catalog/status.
"""

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from shared.files import write_atomic

FEATURE = "catalog"                # in client_hello's "features": the app can browse Apple Music
DEFAULTS = {"on": False, "clean": False, "genres": None, "refresh_hours": 24}
REFRESH_CHOICES = (6, 24, 168)
CATALOG_WAIT_S = 60.0              # the genres answer looks up each genre's #1 playlist
CHART_LIMIT = 25
COVER_MAX = 2 * 1024 * 1024
COVER_PARALLEL = 8
COVER_WAIT_S = 20.0
# A playlist or album not fetched again for this long (nobody opened it
# since): its kept copy, and the covers only it names, go. Genres and
# charts are kept.
ITEMS_KEEP_S = 30 * 86400
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
GENRE_LIMIT = 60                   # the owner's genre list (Apple has about 20)


def clean_settings(changes, current: dict | None = None) -> dict:
    """The owner's apple_music settings: `changes` (any of the keys)
    merged into `current`, each checked; a bad value keeps the old one."""
    s = {**DEFAULTS, **(current or {})}
    if not isinstance(changes, dict):
        return s
    for k in ("on", "clean"):
        if k in changes:
            s[k] = bool(changes[k])
    if "refresh_hours" in changes and changes["refresh_hours"] in REFRESH_CHOICES \
            and not isinstance(changes["refresh_hours"], bool):
        s["refresh_hours"] = changes["refresh_hours"]
    if "genres" in changes:
        g = changes["genres"]
        if g is None:
            s["genres"] = None
        elif isinstance(g, list):
            s["genres"] = list(dict.fromkeys(str(i) for i in g if isinstance(i, (str, int))
                                             and not isinstance(i, bool) and ID_RE.match(str(i))))[:GENRE_LIMIT]
    return s


def cover_name(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def cover_allowed(url) -> bool:
    """Only https links on Apple's image servers are fetched."""
    if not isinstance(url, str) or len(url) > 2000:
        return False
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and host.endswith(".mzstatic.com")


class Key:
    """One list: its topic under catalog/, its kept file and its app command."""

    def __init__(self, what: str, genre: str | None = None, kind: str | None = None, id_: str | None = None):
        self.what, self.genre, self.kind, self.id = what, genre, kind, id_

    @classmethod
    def from_cmd(cls, cmd: dict) -> "Key | None":
        what = cmd.get("what")
        if what == "genres":
            return cls("genres")
        if what == "charts":
            g = cmd.get("genre")
            if g in (None, "", "all"):
                return cls("charts")
            g = str(g) if isinstance(g, (str, int)) and not isinstance(g, bool) else ""
            return cls("charts", genre=g) if ID_RE.match(g) else None
        if what == "items":
            kind, id_ = cmd.get("kind"), cmd.get("id")
            if kind in ("playlist", "album") and isinstance(id_, str) and ID_RE.match(id_):
                return cls("items", kind=kind, id_=id_)
        return None

    @classmethod
    def from_file(cls, name: str) -> "Key | None":
        if name == "genres":
            return cls("genres")
        if name.startswith("charts-"):
            g = name[len("charts-"):]
            return cls("charts", genre=None if g == "all" else g)
        for kind in ("playlist", "album"):
            if name.startswith(f"items-{kind}-"):
                return cls("items", kind=kind, id_=name[len(f"items-{kind}-"):])
        return None

    @property
    def topic(self) -> str:
        if self.what == "charts":
            return f"catalog/charts/{self.genre or 'all'}"
        if self.what == "items":
            return f"catalog/items/{self.kind}/{self.id}"
        return "catalog/genres"

    @property
    def file(self) -> str:
        return self.topic[len("catalog/"):].replace("/", "-") + ".json"

    def command(self) -> tuple[str, dict]:
        if self.what == "charts":
            return "catalog_charts", {"genre": self.genre, "limit": CHART_LIMIT}
        if self.what == "items":
            return "catalog_items", {"kind": self.kind, "id": self.id}
        return "catalog_genres", {}

    def __eq__(self, other):
        return isinstance(other, Key) and self.topic == other.topic

    def __hash__(self):
        return hash(self.topic)


def urls_in(answer: dict) -> set[str]:
    """Every artwork link in an app answer."""
    found = set()

    def add(item):
        if isinstance(item, dict) and isinstance(item.get("artworkUrl"), str):
            found.add(item["artworkUrl"])
    if not isinstance(answer, dict):
        return found
    add(answer)
    for g in answer.get("genres") or []:
        if isinstance(g, dict):
            add(g.get("playlist"))
    for k in ("playlists", "albums", "songs"):
        for item in answer.get(k) or []:
            add(item)
    return found


def track(device_id: str | None, song, cover) -> dict | None:
    """An Apple Music song as a Track the screen can pick: jb-library's
    track shape, plus explicit and catalog."""
    if not isinstance(song, dict) or not song.get("id") or not song.get("title"):
        return None
    d = song.get("duration")
    return {
        "title": str(song["title"]),
        "artist": song.get("artist") or None,
        "album": song.get("album") or None,
        "genre": song.get("genre") or None,
        "artwork_url": cover(song.get("artworkUrl")),
        "item_path": f"helper:{device_id}/catalog:{song['id']}" if device_id else None,
        "duration_ms": int(d * 1000) if isinstance(d, (int, float)) and not isinstance(d, bool) else None,
        "explicit": bool(song.get("explicit")),
        "catalog": True,
    }


def playlist(p, cover) -> dict | None:
    if not isinstance(p, dict) or not p.get("id"):
        return None
    return {"id": str(p["id"]), "name": p.get("name") or None, "curator": p.get("curator") or None,
            "cover": cover(p.get("artworkUrl"))}


def album(a, cover) -> dict | None:
    if not isinstance(a, dict) or not a.get("id"):
        return None
    return {"id": str(a["id"]), "name": a.get("name") or None, "artist": a.get("artist") or None,
            "explicit": bool(a.get("explicit")), "cover": cover(a.get("artworkUrl"))}


def genre_list(answer: dict, cover) -> list:
    """Every genre the app sent, in Apple's order, with its tile's cover."""
    out = []
    for g in (answer or {}).get("genres") or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        p = playlist(g.get("playlist"), cover)
        out.append({"id": str(g["id"]), "name": g.get("name") or None,
                    "cover": p["cover"] if p else None, "playlist": p})
    return out


def shape(key: Key, kept: dict | None, settings: dict, device_id: str | None, cover,
          genre_names: dict | None = None) -> dict:
    """The payload for one list's topic (state and reason added by the
    caller), from its kept copy, with the owner's filters applied.
    `cover(url)`: "/covers/<sha1>.jpg" once downloaded, else None."""
    out: dict = {"v": 1, "at": (kept or {}).get("at")}
    answer = (kept or {}).get("answer")
    clean = settings.get("clean")

    def songs(items):
        tracks = [track(device_id, s, cover) for s in items or []]
        return [t for t in tracks if t and not (clean and t["explicit"])]

    if key.what == "genres":
        if not answer:
            return out
        every = genre_list(answer, cover)
        wanted = settings.get("genres")
        if wanted is None:
            shown = every
        else:
            by_id = {g["id"]: g for g in every}
            shown = [by_id[i] for i in wanted if i in by_id]
        out["genres"] = shown
        # Every genre, ticked or not: what the admin's Genres list offers.
        out["all"] = [{"id": g["id"], "name": g["name"], "cover": g["cover"]} for g in every]
        out["storefront"] = answer.get("storefront")
    elif key.what == "charts":
        out["genre"] = {"id": key.genre, "name": (genre_names or {}).get(key.genre)} if key.genre else None
        if not answer:
            return out
        out["playlists"] = [p for p in (playlist(i, cover) for i in answer.get("playlists") or []) if p]
        out["albums"] = [a for a in (album(i, cover) for i in answer.get("albums") or [])
                         if a and not (clean and a["explicit"])]
        out["songs"] = songs(answer.get("songs"))
    else:
        out.update({"kind": key.kind, "id": key.id})
        if not answer:
            return out
        out.update({"name": answer.get("name") or None, "artist": answer.get("artist") or None,
                    "cover": cover(answer.get("artworkUrl")), "songs": songs(answer.get("songs"))})
    return out


async def fetch_url(url: str) -> bytes | None:
    """One cover off Apple's image servers (no redirects: they could lead
    anywhere), or None."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=COVER_WAIT_S)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as r:
                if r.status != 200 or (r.content_length or 0) > COVER_MAX:
                    return None
                body = await r.content.read(COVER_MAX + 1)
                # read(n) gives what has arrived so far: keep reading to the end.
                while len(body) <= COVER_MAX:
                    more = await r.content.read(COVER_MAX + 1 - len(body))
                    if not more:
                        break
                    body += more
                return body if len(body) <= COVER_MAX else None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None


class Catalog:
    """The lists, their kept copies and covers, and catalog/status. `helper`
    is source.py's Helper: the bus, the phones, the settings."""

    def __init__(self, helper, data_dir: Path, fetch=fetch_url):
        self.helper = helper
        self.dir = data_dir / "catalog"
        self.art_dir = data_dir / "artwork"
        self.fetch = fetch
        self.fetching: set[Key] = set()
        self.songs: dict[str, dict] = {}        # catalog song id -> its last published Track
        self._status: dict | None = None
        self._phones = self._read_json(self.dir / "phones.json")   # device -> features, canPlayCatalog
        self._remember_kept()

    # -- files -------------------------------------------------------------

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_json(self, path: Path, data) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(data))

    def kept(self, key: Key) -> dict | None:
        return self._read_json(self.dir / key.file) or None

    def kept_keys(self) -> list[Key]:
        if not self.dir.is_dir():
            return []
        keys = [Key.from_file(p.stem) for p in sorted(self.dir.glob("*.json"))]
        return [k for k in keys if k]

    def settings(self) -> dict:
        return clean_settings(None, self.helper._settings.get("apple_music"))

    def cover(self, url) -> str | None:
        if not cover_allowed(url):
            return None
        name = cover_name(url)
        return f"/covers/{name}.jpg" if (self.art_dir / f"{name}.jpg").is_file() else None

    def _remember_kept(self) -> None:
        """The songs in the kept lists, so a catalog song playing after a
        restart still gets its cover on Now Playing."""
        for key in self.kept_keys():
            answer = (self.kept(key) or {}).get("answer") or {}
            for s in answer.get("songs") or []:
                t = track("-", s, self.cover)
                if t:
                    self.songs[str(s["id"])] = t

    # -- the phones ----------------------------------------------------------

    def heard_hello(self, device_id: str, features) -> None:
        """client_hello's features (an older app sends none)."""
        f = sorted({str(x) for x in features if isinstance(x, str)}) if isinstance(features, list) else []
        p = self._phones.setdefault(device_id, {})
        if p.get("features") != f:
            p["features"] = f
            self._write_json(self.dir / "phones.json", self._phones)

    def heard_music(self, device_id: str, music: dict) -> None:
        """music_status: whether this phone can play Apple Music itself.
        Kept, so an asleep phone's answer still counts."""
        can = music.get("canPlayCatalog")
        if isinstance(can, bool) and self._phones.setdefault(device_id, {}).get("canPlayCatalog") is not can:
            self._phones[device_id]["canPlayCatalog"] = can
            self._write_json(self.dir / "phones.json", self._phones)

    def can_browse(self, device_id: str | None) -> bool:
        return FEATURE in ((self._phones.get(device_id) or {}).get("features") or [])

    def forget(self, device_id: str) -> None:
        if self._phones.pop(device_id, None) is not None:
            self._write_json(self.dir / "phones.json", self._phones)

    def status(self) -> dict:
        s = self.settings()
        d = self.helper.active
        if not s["on"]:
            reason = "off"
        elif not d or not self.helper.reachable(d):
            reason = "no_phone"
        elif not self.can_browse(d):
            reason = "app_update"
        elif (self._phones.get(d) or {}).get("canPlayCatalog") is not True:
            reason = "no_subscription"
        else:
            reason = None
        storefront = ((self.kept(Key("genres")) or {}).get("answer") or {}).get("storefront")
        return {"v": 1, "on": s["on"], "available": reason is None, "reason": reason, "storefront": storefront}

    async def publish_status(self, force: bool = False) -> None:
        status = self.status()
        if force or status != self._status:
            self._status = status
            await self.helper.bus.publish(f"{self.helper.topic}/catalog/status", status, retain=True)

    # -- publishing ------------------------------------------------------------

    def _device(self, kept: dict | None) -> str | None:
        """Whose phone plays its songs: the source, else the one that sent the list."""
        return self.helper.active or (kept or {}).get("device_id")

    def _genre_names(self) -> dict:
        answer = (self.kept(Key("genres")) or {}).get("answer") or {}
        return {str(g["id"]): g.get("name") for g in answer.get("genres") or [] if isinstance(g, dict) and g.get("id")}

    async def publish(self, key: Key, state: str, reason: str | None = None, kept: dict | None = None) -> None:
        kept = kept if kept is not None else self.kept(key)
        payload = shape(key, kept, self.settings(), self._device(kept), self.cover,
                        self._genre_names() if key.what == "charts" else None)
        payload["state"], payload["reason"] = state, reason
        for t in payload.get("songs") or []:
            if t["item_path"]:
                self.songs[t["item_path"].rpartition("/catalog:")[2]] = t
        await self.helper.bus.publish(f"{self.helper.topic}/{key.topic}", payload, retain=True)

    async def republish(self) -> None:
        """Every kept list again: the owner changed clean or genres."""
        for key in self.kept_keys():
            await self.publish(key, "loading" if key in self.fetching else "ready")

    # -- asking ----------------------------------------------------------------

    async def request(self, cmd: dict) -> None:
        """jukebox/jb-source-helper/cmd {"action": "catalog", ...}."""
        if cmd.get("what") == "all":
            await self.refresh_all()
            return
        key = Key.from_cmd(cmd)
        if key is None:
            return
        kept = self.kept(key)
        hours = self.settings()["refresh_hours"]
        fresh = kept and not kept.get("stale") and time.time() - (kept.get("at") or 0) < hours * 3600
        if key in self.fetching:
            await self.publish(key, "loading", kept=kept)     # a screen that just subscribed
            return
        if fresh and not cmd.get("refresh"):
            await self.publish(key, "ready", kept=kept)
            return
        await self.fetch_list(key, kept)

    async def refresh_all(self) -> None:
        """The admin's Refresh now: the Apple Music screen's lists at once,
        and every other kept list on its next request."""
        for key in self.kept_keys():
            if key.what == "items" or key.genre:
                kept = self.kept(key)
                if kept:
                    kept["stale"] = True
                    self._write_json(self.dir / key.file, kept)
        await asyncio.gather(self.fetch_list(Key("genres"), self.kept(Key("genres"))),
                             self.fetch_list(Key("charts"), self.kept(Key("charts"))))

    async def fetch_list(self, key: Key, kept: dict | None) -> None:
        if key in self.fetching:
            return
        status = self.status()
        if not status["available"]:
            await self.publish(key, "error", status["reason"], kept=kept)
            return
        self.fetching.add(key)
        try:
            await self.publish(key, "loading", kept=kept)
            type_, payload = key.command()
            device = self.helper.active
            try:
                answer = await self.helper.catalog_command(device, type_, payload, CATALOG_WAIT_S)
            except Exception as e:                  # source.CommandError: the app's reason
                reason = getattr(e, "reason", "other")
                self.helper.log.info("Apple Music %s: %s", key.topic, reason)
                if reason == "no_subscription" and device:
                    self.heard_music(device, {"canPlayCatalog": False})
                    await self.publish_status()
                await self.publish(key, "error", reason, kept=kept)
                return
            kept = {"v": 1, "at": time.time(), "device_id": device, "answer": answer if isinstance(answer, dict) else {}}
            await self.download_covers(urls_in(kept["answer"]))
            self._write_json(self.dir / key.file, kept)
        finally:
            self.fetching.discard(key)
        await self.publish(key, "ready", kept=kept)
        if key.what == "genres":
            await self.publish_status()             # the storefront
        # Here, not in a thread: another list's covers are only listed in
        # covers.json once its kept copy is written, with no wait between.
        self.clean_up()

    # -- covers ----------------------------------------------------------------

    def _covers(self) -> set[str]:
        """The covers this module downloaded (the folder also holds the app's
        own uploads, which aren't ours to delete)."""
        data = self._read_json(self.dir / "covers.json")
        return set(data.get("covers") or [])

    async def download_covers(self, urls: set[str]) -> int:
        wanted = {cover_name(u): u for u in urls if cover_allowed(u)}
        missing = {n: u for n, u in wanted.items() if not (self.art_dir / f"{n}.jpg").is_file()}
        if not missing:
            return 0
        gate = asyncio.Semaphore(COVER_PARALLEL)
        got: list[str] = []

        async def one(name, url):
            async with gate:
                body = await self.fetch(url)
            if body and len(body) <= COVER_MAX and body.startswith(b"\xff\xd8"):
                self.art_dir.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(write_atomic, self.art_dir / f"{name}.jpg", body)
                got.append(name)
        await asyncio.gather(*(one(n, u) for n, u in missing.items()))
        if got:
            self._write_json(self.dir / "covers.json", {"covers": sorted(self._covers() | set(got))})
        return len(got)

    def clean_up(self, now: float | None = None) -> int:
        """Drops playlists and albums nobody opened for ITEMS_KEEP_S, then the
        covers no kept list names. Returns how many covers went."""
        now = now if now is not None else time.time()
        named = set()
        for key in self.kept_keys():
            kept = self.kept(key) or {}
            if key.what == "items" and now - (kept.get("at") or 0) > ITEMS_KEEP_S:
                (self.dir / key.file).unlink(missing_ok=True)
                continue
            named |= {cover_name(u) for u in urls_in(kept.get("answer") or {}) if cover_allowed(u)}
        ours = self._covers()
        gone = ours - named
        for name in gone:
            (self.art_dir / f"{name}.jpg").unlink(missing_ok=True)
        if gone:
            self._write_json(self.dir / "covers.json", {"covers": sorted(ours & named)})
        return len(gone)

    # -- now playing -------------------------------------------------------------

    def now_playing(self, np: dict, item_id: str) -> dict:
        """A catalog song's now-playing: its cover, album and catalog: true
        from the Track the screen was given."""
        if not item_id.startswith("catalog:"):
            return np
        t = self.songs.get(item_id[len("catalog:"):]) or {}
        return {**np, "album": np.get("album") or t.get("album"), "artwork_url": t.get("artwork_url"),
                "genre": np.get("genre") or t.get("genre"), "catalog": True}
