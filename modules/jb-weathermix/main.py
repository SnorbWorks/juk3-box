"""Weather Mix: a mood playlist for the weather, from the songs on the
connected phone (docs/contracts/extensions.md, playlist blocks).

It listens to Weather and the library. When the mood changes (the weather,
the time of day or the season; checked at most hourly), or once a day so
it doesn't go stale, it asks Last.fm for the top tracks and artists with
the mood's tags, keeps the ones in the library, and publishes the mix.
Nothing about the library is kept: only the current mix, and the last few
names it used. Without a Last.fm key it picks by the library's genres.
"""

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path

import aiohttp

import moods
from shared.bus import Bus
from shared.module_sdk import module_id, settings, state_dir

MODULE = module_id() or "jb-weathermix"
TOPIC = f"jukebox/{MODULE}/mix"
MANIFEST = json.loads((Path(__file__).parent / "module.json").read_text())
DEFAULTS = {k: v.get("default") for k, v in MANIFEST.get("owner_settings", {}).items()}
LASTFM = "https://ws.audioscrobbler.com/2.0/"
REBUILD_EVERY_S = 24 * 3600
CHECK_EVERY_S = 3600
MIN_SONGS = 10

log = logging.getLogger(MODULE)


def norm(s: str) -> str:
    """A song or artist name to compare by: lowercase, no "(feat. ...)",
    "- Remastered" and the like, no punctuation."""
    s = str(s or "").lower()
    s = re.sub(r"\s*[\(\[][^)\]]*(feat|with|remaster|version|edit|live|mono|stereo)[^)\]]*[\)\]]", "", s)
    s = re.sub(r"\s+-\s+.*(remaster|version|edit|live|mono|stereo).*$", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


async def lastfm(session: aiohttp.ClientSession, key: str, method: str, tag: str, limit: int) -> list[dict]:
    async with session.get(LASTFM, params={"method": method, "tag": tag, "limit": limit, "api_key": key, "format": "json"}) as r:
        data = await r.json(content_type=None)
    if method == "tag.gettoptracks":
        return (data.get("tracks") or {}).get("track") or []
    return (data.get("topartists") or {}).get("artist") or []


async def scores_from_lastfm(session, key: str, tags: dict[str, float], library: list[dict]) -> dict[int, float]:
    """Each library song's fit for the tags: a song tagged with one counts
    3x the tag's weight; a song by an artist tagged with it, 1x."""
    by_song: dict[tuple[str, str], list[int]] = {}
    by_artist: dict[str, list[int]] = {}
    for i, t in enumerate(library):
        by_song.setdefault((norm(t.get("title")), norm(t.get("artist"))), []).append(i)
        by_artist.setdefault(norm(t.get("artist")), []).append(i)
    scores: dict[int, float] = {}
    for tag, w in tags.items():
        for t in await lastfm(session, key, "tag.gettoptracks", tag, 1000):
            for i in by_song.get((norm(t.get("name")), norm((t.get("artist") or {}).get("name"))), []):
                scores[i] = scores.get(i, 0) + 3 * w
        await asyncio.sleep(0.3)
        for a in await lastfm(session, key, "tag.gettopartists", tag, 500):
            for i in by_artist.get(norm(a.get("name")), []):
                scores[i] = scores.get(i, 0) + w
        await asyncio.sleep(0.3)
    return scores


def scores_from_genres(mood: str, library: list[dict]) -> dict[int, float]:
    genres = moods.MOODS[mood]["genres"]
    return {i: (len(genres) - genres.index(t["genre"])) / len(genres)
            for i, t in enumerate(library) if t.get("genre") in genres}


def choose(scores: dict[int, float], library: list[dict], size: int, rng: random.Random) -> list[dict]:
    """The best-fitting songs, with variety: at most two per artist, picked
    from a pool a little bigger than the mix so each one differs."""
    ranked = sorted(scores, key=lambda i: scores[i] + rng.random() * 0.5, reverse=True)
    pool = ranked[: size * 3]
    rng.shuffle(pool)
    pool.sort(key=lambda i: scores[i], reverse=True)
    picked, per_artist, seen = [], {}, set()
    for i in pool:
        a = norm(library[i].get("artist"))
        song = (norm(library[i].get("title")), a)
        if per_artist.get(a, 0) >= 2 or song in seen:      # a song the phone has twice counts once
            continue
        seen.add(song)
        per_artist[a] = per_artist.get(a, 0) + 1
        picked.append(library[i])
        if len(picked) >= size:
            break
    rng.shuffle(picked)
    return picked


class Mixer:
    def __init__(self):
        self.weather: dict | None = None
        self.library: list[dict] = []
        self.key: tuple | None = None       # what the current mix was made for
        self.made_at = 0.0
        self.state = state_dir() / "recent-names.json"

    def recent(self) -> list[str]:
        try:
            return json.loads(self.state.read_text())
        except (OSError, ValueError):
            return []

    def remember(self, name: str):
        try:
            self.state.write_text(json.dumps(([name] + self.recent())[:8]))
        except OSError:
            pass

    async def build(self, bus, session, s: dict, force=False):
        if not self.weather or not self.library:
            return
        now = datetime.now()
        mood = moods.mood_of(self.weather)
        part, when = moods.daypart(now.hour), moods.season(now.month)
        size = int(s.get("size") or 25)
        key = (mood, part, when, size, len(self.library) // 25)
        if not force and key == self.key and time.time() - self.made_at < REBUILD_EVERY_S:
            return
        rng = random.Random()
        tags = moods.recipe(mood, part, when)
        source = "genres"
        scores = {}
        api_key = str(s.get("lastfm-key") or "").strip()
        if api_key:
            try:
                scores = await scores_from_lastfm(session, api_key, tags, self.library)
                source = "last.fm"
            except Exception as e:
                log.warning("Last.fm: %s; using genres", e)
        songs = choose(scores, self.library, size, rng)
        if len(songs) < MIN_SONGS:
            # Thin for this library: fill up from the mood's genres.
            have = {(t.get("title"), t.get("artist")) for t in songs}
            more = choose(scores_from_genres(mood, self.library), self.library, size, rng)
            songs += [t for t in more if (t.get("title"), t.get("artist")) not in have][: size - len(songs)]
        if not songs:
            log.info("no songs fit %s yet", mood)
            return
        name = moods.name_for(mood, part, when, self.recent(), rng)
        self.remember(name)
        w = self.weather
        await bus.publish(TOPIC, {
            "v": 1,
            "hook": name,
            "subtitle": " and ".join(x for x in (str(w.get("temp") or ""), str(w.get("condition") or "").lower()) if x),
            "icon": moods.ICONS[mood],
            "mood": mood,
            "songs": [{"title": t.get("title"), "artist": t.get("artist")} for t in songs],
            "autoplay_after": int(s.get("autoplay") or 0),
            "source": source,
            "made": time.time(),
        }, retain=True)
        self.key, self.made_at = key, time.time()
        log.info("%s (%s, %s %s): %d songs from %s", name, mood, part, when, len(songs), source)


async def run():
    mixer = Mixer()
    while True:
        try:
            async with Bus(MODULE) as bus, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                heartbeat = asyncio.create_task(bus.run_heartbeat())
                wake = asyncio.Event()
                rebuild = {"force": False}

                async def listen():
                    msgs = await bus.subscribe("jukebox/jb-weather/current")
                    await bus._client.subscribe("jukebox/jb-library/tracks")
                    await bus._client.subscribe("jukebox/jb-admin/changed")
                    async for topic, payload in msgs:
                        if topic.endswith("jb-weather/current"):
                            mixer.weather = payload
                            wake.set()
                        elif topic.endswith("jb-library/tracks"):
                            mixer.library = [t for t in payload.get("tracks") or [] if isinstance(t, dict) and t.get("title")]
                            wake.set()
                        elif f"module:{MODULE}" in (payload.get("what") or []):
                            rebuild["force"] = True       # its settings changed
                            wake.set()

                listener = asyncio.create_task(listen())
                try:
                    while True:
                        try:
                            await mixer.build(bus, session, settings(DEFAULTS), force=rebuild["force"])
                        except Exception as e:
                            log.warning("couldn't make a mix: %s", e)
                        rebuild["force"] = False
                        wake.clear()
                        try:
                            await asyncio.wait_for(wake.wait(), timeout=CHECK_EVERY_S)
                        except asyncio.TimeoutError:
                            pass
                        await asyncio.sleep(2)   # let a burst of messages settle
                finally:
                    heartbeat.cancel()
                    listener.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("bus: %s; retrying", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    asyncio.run(run())
