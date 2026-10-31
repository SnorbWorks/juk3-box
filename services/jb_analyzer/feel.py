"""The feel: what kind of song this is, summed up the way a lighting
designer would before the first cue. Once a song: live it settles over the
first seconds as the listeners' tags, the ears and the audio come in; the
study (study.py) works it out again from the whole recording and keeps it
in the song's map.

From everything the analyzer knows about a song:
  tags    listeners' words (tags.py): genres, moods, decades
  genre   the source's own genre field
  year    the source's year (the Helper app's library)
  audio   tempo, how busy the drums are (0..1), brightness (0..1)
  ears    the ears' groups, averaged (ears.hear): voice, drums, guitar,
          synth..., and music's moods (happy, sad, tender, exciting, angry,
          scary) -- tiny numbers in a full mix, so read against each other
  key     the key and mode (texture.Key): "major" -1 (minor) .. 1 (major)
  voiced  how much of the song has a voice in it, 0..1 (the map's sections)

-> {"energy", "valence" (sad 0 .. happy 1), "tender", "aggressive",
    "groove", "moody", "pace" (the tempo as the lights should move: slow 0 ..
    fast 1), "voice", "era" (a decade like "80s", or None), "words" (what
    the owner sees: ["moody", "slow", "vocals"])}, all 0..1. Pure.
"""

import re

import numpy as np

TENDER = {"love songs", "love song", "romantic", "romance", "ballad", "ballads", "slow", "soft", "tender",
          "sweet", "lovely", "wedding", "slow jams", "love ballad", "soft rock", "lullaby", "sensual"}
MOODY = {"moody", "dark", "atmospheric", "melancholy", "melancholic", "sad", "depressing", "haunting",
         "brooding", "trip-hop", "trip hop", "dream pop", "shoegaze", "darkwave", "dark wave", "ethereal",
         "emotional", "nocturnal", "gothic", "post-punk", "dreamy", "downtempo", "chillwave", "ambient"}
AGGRESSIVE = {"metal", "heavy metal", "thrash metal", "death metal", "black metal", "hardcore", "aggressive",
              "hard rock", "punk", "punk rock", "heavy", "industrial", "screamo", "metalcore", "grunge",
              "angry", "nu metal", "hardstyle", "dubstep", "brostep"}
GROOVE = {"dance", "funk", "disco", "house", "groovy", "dancehall", "electro", "party", "club", "reggaeton",
          "boogie", "nu-disco", "nu disco", "dance-pop", "edm", "techno", "hip-hop", "hip hop", "rnb", "r&b",
          "motown", "latin", "salsa", "afrobeat", "electropop", "synthpop", "reggae", "ska", "dub", "cumbia",
          "samba", "soca", "bachata"}
HAPPY = {"happy", "upbeat", "fun", "feel good", "feelgood", "summer", "party", "joyful", "uplifting",
         "cheerful", "sunny", "positive", "good mood"}
SAD = {"sad", "melancholy", "melancholic", "depressing", "heartbreak", "lonely", "breakup", "sorrow",
       "tearjerker"}
CALM = {"chill", "chillout", "mellow", "relaxing", "calm", "ballad", "slow", "soft", "dreamy", "ambient",
        "acoustic", "lullaby", "sleep", "lo-fi", "easy listening", "downtempo"}
ENERGETIC = {"energetic", "upbeat", "party", "workout", "aggressive", "fast", "hype", "banger", "driving",
             "anthem", "hard rock", "metal", "punk", "edm", "drum and bass"}
DECADE_RE = re.compile(r"^(?:19)?([5-9]0)s$|^(?:20)?(00|10|20)s$|^(19[5-9]0|20[0-2]0)s$")
MOODS = ("happy", "sad", "tender", "exciting", "angry", "scary")


def _clip(x) -> float:
    return float(np.clip(x, 0.0, 1.0))


def era_of(year=None, tags=()) -> str | None:
    """The decade: the source's year, else a listeners' tag like "80s"."""
    if isinstance(year, int) and 1900 < year < 2100:
        return f"{year % 100 // 10 * 10:02d}s"
    for t in tags:
        m = DECADE_RE.match(t.strip().lower())
        if m:
            d = next(g for g in m.groups() if g)
            return f"{int(d) % 100:02d}s"
    return None


def feel(tags=(), genre=None, year=None, audio=None, ears=None, key=None, voiced=None) -> dict:
    words = {str(w).strip().lower() for w in tags if w} | ({str(genre).strip().lower()} if genre else set())
    split = {p for w in words for p in re.split(r"[\s/&-]+", w) if p}

    def tagged(vocab) -> float:
        """1 for a tag that says it outright, 0.6 for one that has the word in it."""
        if words & vocab:
            return 1.0
        return 0.6 if any(v in split for v in vocab if " " not in v) else 0.0

    audio = audio or {}
    tempo = audio.get("tempo")
    pace_raw = 0.5 if not tempo else _clip((tempo - 60) / 90)
    drive = audio.get("drums")
    drive = 0.5 if drive is None else _clip(drive)
    bright = audio.get("brightness")
    bright = 0.5 if bright is None else _clip(bright)

    ears = ears or {}
    moods = ears.get("moods", ears)
    total = sum(float(moods.get(m, 0.0)) for m in MOODS)
    share = {m: (float(moods.get(m, 0.0)) / total if total > 1e-4 else 1 / len(MOODS)) for m in MOODS}
    heard = total > 1e-4

    major = float((key or {}).get("major", 0.0)) * float((key or {}).get("sure", 0.0))

    tender = _clip(0.65 * tagged(TENDER) + (0.6 * share["tender"] if heard else 0.0) + 0.2 * (1 - pace_raw)
                   - 0.2 * drive)
    aggressive = _clip(0.75 * tagged(AGGRESSIVE) + (0.8 * share["angry"] + 0.3 * share["scary"] if heard else 0.0)
                       + 0.15 * bright * drive)
    groove = _clip(0.5 * tagged(GROOVE) + 0.35 * drive * (1 - abs(pace_raw - 0.6) * 1.5)
                   + (0.2 * share["exciting"] if heard else 0.0))
    valence = _clip(0.5 + 0.18 * major + (0.4 * (share["happy"] - share["sad"]) if heard else 0.0)
                    + 0.2 * tagged(HAPPY) - 0.25 * tagged(SAD))
    moody = _clip(0.6 * tagged(MOODY) + 0.35 * (1 - valence) + 0.15 * max(0.0, -major)
                  + (0.3 * (share["sad"] + share["scary"]) if heard else 0.0) - 0.2 * tagged(HAPPY))
    energy = _clip(0.35 * pace_raw + 0.3 * drive + 0.15 * bright + (0.25 * share["exciting"] if heard else 0.05)
                   + 0.15 * tagged(ENERGETIC) - 0.15 * tagged(CALM) - 0.15 * tender + 0.1 * aggressive)
    # How the lights should move: the tempo, held back for a tender or a moody song, pushed for a hard one.
    pace = _clip(pace_raw - 0.25 * tender - 0.15 * moody + 0.1 * aggressive)
    voice = 0.5 if voiced is None else _clip(voiced)
    era = era_of(year, sorted(words))

    said = []
    for name, v, word in (("tender", tender, "love song"), ("moody", moody, "moody"),
                          ("aggressive", aggressive, "hard"), ("groove", groove, "groovy")):
        if v >= 0.55:
            said.append(word)
    if valence >= 0.68 and energy >= 0.5:
        said.append("upbeat")
    said.append("slow" if pace < 0.3 else "fast" if pace > 0.75 else "mid-tempo")
    if voice >= 0.6:
        said.append("vocals")
    elif voice < 0.25:
        said.append("instrumental")
    if era:
        said.append(era)
    return {"energy": round(energy, 3), "valence": round(valence, 3), "tender": round(tender, 3),
            "aggressive": round(aggressive, 3), "groove": round(groove, 3), "moody": round(moody, 3),
            "pace": round(pace, 3), "voice": round(voice, 3), "era": era, "words": said}
