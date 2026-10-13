"""Song tags from Last.fm: what listeners call this song.

The slow layer's other half. The audio says how a song sounds right now;
listeners' tags say what it *is* -- "synthwave", "80s", "melancholy",
"chill". Looked up by the title and artist the source already gives us (no
fingerprinting needed while that metadata exists): the track's top tags, or
the artist's when the track has too few. Cached on disk, so each song is
asked about once, and a jukebox without internet just goes without.

Pure helpers (clean_tags, genre_of, mood_nudge) are separate from the
network call so they can be tested offline.
"""

import json
import logging
import time
from pathlib import Path

import aiohttp

API = "https://ws.audioscrobbler.com/2.0/"
CACHE_PATH = Path("/var/lib/juk3-box/lastfm-tags.json")
MAX_TAGS = 6
MIN_COUNT = 10            # Last.fm counts are 0..100 relative to the top tag
TIMEOUT_S = 8
RETRY_FAILED_S = 3600     # a failed lookup (offline, API down) is retried after this

log = logging.getLogger(__name__)

# Tags that say nothing about the music.
NOISE = {
    "seen live", "favorites", "favourites", "favorite", "favourite", "my favorites", "love",
    "loved", "awesome", "beautiful", "amazing", "good", "great", "best", "cool", "music",
    "albums i own", "spotify", "under 2000 listeners", "check out", "fip", "radio",
}

# The first tag in this list (in the track's own tag order) is its genre.
GENRES = [
    "rock", "classic rock", "hard rock", "soft rock", "alternative", "alternative rock", "indie",
    "indie rock", "indie pop", "pop", "synthpop", "synthwave", "new wave", "electronic", "electronica",
    "dance", "edm", "house", "techno", "trance", "dubstep", "drum and bass", "disco", "funk", "soul",
    "rnb", "r&b", "hip-hop", "hip hop", "rap", "jazz", "blues", "country", "folk", "americana",
    "metal", "heavy metal", "punk", "punk rock", "grunge", "reggae", "ska", "latin", "classical",
    "soundtrack", "ambient", "lo-fi", "singer-songwriter", "motown", "rockabilly", "psychedelic",
    "progressive rock", "emo", "k-pop", "gospel",
]

# Listener mood words -> a nudge to the audio's arousal (calm..energetic).
CALM = {"chill", "chillout", "mellow", "relaxing", "calm", "sad", "melancholy", "melancholic",
        "ballad", "slow", "soft", "dreamy", "ambient", "acoustic", "lullaby", "sleep", "lo-fi"}
ENERGETIC = {"energetic", "upbeat", "party", "dance", "workout", "aggressive", "fast",
             "happy", "fun", "hype", "banger", "driving", "anthem"}
NUDGE = 0.12


def clean_tags(raw: list, artist: str | None) -> list:
    """Last.fm's tag list -> up to MAX_TAGS lowercase names worth keeping."""
    out = []
    a = (artist or "").strip().lower()
    for t in raw:
        name = str(t.get("name", "")).strip().lower()
        try:
            count = int(t.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        if not name or count < MIN_COUNT or name in NOISE or name == a or name in out:
            continue
        out.append(name)
        if len(out) == MAX_TAGS:
            break
    return out


def genre_of(tags: list) -> str | None:
    for t in tags:
        if t in GENRES:
            return t
    return None


def mood_nudge(tags: list) -> float:
    """-NUDGE..+NUDGE for arousal: what listeners feel, which audio alone can miss."""
    calm = sum(t in CALM for t in tags)
    energetic = sum(t in ENERGETIC for t in tags)
    if calm == energetic:
        return 0.0
    return NUDGE if energetic > calm else -NUDGE


class TagLookup:
    def __init__(self, api_key: str | None, cache_path: Path = CACHE_PATH):
        self.key = api_key
        self.path = cache_path
        self.failed = {}
        try:
            self.cache = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.cache = {}

    @staticmethod
    def _cache_key(artist, title):
        return f"{artist.strip().lower()}\n{title.strip().lower()}"

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache))
            tmp.replace(self.path)
        except OSError:
            log.warning("couldn't save the tag cache at %s", self.path)

    async def _get(self, session, **params):
        params.update(api_key=self.key, format="json", autocorrect=1)
        async with session.get(API, params=params, timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as r:
            data = await r.json(content_type=None)
        if "error" in data:
            return []
        return data.get("toptags", {}).get("tag", []) or []

    async def lookup(self, artist: str | None, title: str | None) -> list | None:
        """Tags for a song, from the cache or Last.fm; None if unknown."""
        if not self.key or not artist or not title:
            return None
        k = self._cache_key(artist, title)
        if k in self.cache:
            return self.cache[k]
        if time.monotonic() - self.failed.get(k, -1e9) < RETRY_FAILED_S:
            return None
        try:
            async with aiohttp.ClientSession() as s:
                tags = clean_tags(await self._get(s, method="track.gettoptags", artist=artist, track=title), artist)
                if len(tags) < 3:
                    more = clean_tags(await self._get(s, method="artist.gettoptags", artist=artist), artist)
                    tags += [t for t in more if t not in tags][:MAX_TAGS - len(tags)]
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            log.info("tag lookup failed for %s - %s: %s", artist, title, e)
            self.failed[k] = time.monotonic()
            return None
        self.cache[k] = tags
        self._save()
        return tags
