"""Show styles: how the cabinet dresses each kind of music.

A style is what a lighting designer brings to a genre: its palettes, how
hard it hits, whether it strobes, what drives the rhythm, and which
movement each part of the cabinet makes in each kind of moment. The
director (director.py) picks one per song -- from the song's genre and
listeners' tags (jb-analyzer's slow channel), else from its mood -- and the
parts (parts.py) draw it.

Every part has a job (docs/Light Show Styles.md, from pro practice):
  upper   the storyteller: sections, colour, the build and the drop
  foot    the rhythm section: the kick (or snare, or off-beat) made visible
  bar     the secondary voice: the music itself (spectrum, level, a
          ribbon), or a second rhythm (a drum kit seen from above, a beat
          counter, ripples, a bouncing ball) -- changing every 8 bars and
          at each section, so it never settles into one look
  keys    the sparkle: hats and accents on the keypad

Scenes are listed per tier -- calm (verses, breakdowns, quiet songs), main,
peak (choruses, drops) -- as alternatives: each song picks its own, and
they rotate every so many bars so a long song doesn't repeat itself.
"""

import hashlib
from dataclasses import dataclass, field


def hexrgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))


def P(*hexes):
    return tuple(hexrgb(h) for h in hexes)


@dataclass(frozen=True)
class Style:
    name: str
    palettes: tuple                 # each a few colours; a new section moves to the next
    peak: tuple                     # the palette for drops and big choruses
    light: tuple = (0.3, 1.0)       # (floor, ceiling): how dark the calm gets, how bright the peak
    strobe: int = 1                 # 0 never flash white, 1 on big hits and drops, 2 also strobe the builds
    gap: str = "blackout"           # on a gap: "blackout", "dim", or "" (ignore)
    pace: str = "beat"              # "half": move on every other beat; "beat"; "double"
    drive: str = "kick"             # what the footlights follow: kick, snare, offbeat, level
    release: float = 0.2            # seconds for a pulse to fade (long = smooth, short = punchy)
    beat_locked: bool = True        # False: swing and rubato fool beat clocks, so follow the level
    upper: dict = field(default_factory=dict)
    foot: dict = field(default_factory=dict)
    bar: dict = field(default_factory=dict)
    keys: str = "sparkle"


def _tiers(calm, main, peak):
    return {"calm": tuple(calm), "main": tuple(main), "peak": tuple(peak)}


STYLES = {s.name: s for s in [
    Style("edm",
          palettes=(P("#FF00C8", "#00E5FF", "#7A00FF"), P("#00E5FF", "#2040FF", "#B000FF"),
                    P("#7A00FF", "#FF2A9A", "#1A1AFF"), P("#00FF9C", "#00B0FF", "#FF00C8")),
          peak=P("#FFFFFF", "#00E5FF", "#FF00C8"),
          light=(0.2, 1.0), strobe=2, pace="beat", drive="kick", release=0.18,
          upper=_tiers(["breathe", "ribbon"], ["chase", "sweep", "eq"], ["chase", "slab"]),
          foot=_tiers(["glow"], ["pulse", "rows"], ["rows", "chase"]),
          bar=_tiers(["ribbon", "trace"], ["spectrum", "tiles", "drumkit", "ripple", "comet", "classic"], ["mirror", "ripple", "sparks", "counter"]),
          keys="sparkle"),
    Style("bass",
          palettes=(P("#39FF14", "#7A00FF"), P("#FF0022", "#7A00FF"), P("#39FF14", "#00E5FF")),
          peak=P("#FFFFFF", "#39FF14"),
          light=(0.0, 1.0), strobe=2, pace="half", drive="snare", release=0.15,
          upper=_tiers(["breathe"], ["slab", "wobble"], ["wobble", "slab"]),
          foot=_tiers(["glow"], ["pulse"], ["pulse", "rows"]),
          bar=_tiers(["vu", "trace"], ["mirror", "drumkit", "tiles"], ["ripple", "sparks", "mirror"]),
          keys="pulse"),
    Style("hiphop",
          palettes=(P("#FFB000", "#6A0DAD"), P("#B0102A", "#FFB000"), P("#6A0DAD", "#FF2A9A"),
                    P("#FFB000", "#20103A")),
          peak=P("#FFD060", "#FFFFFF", "#6A0DAD"),
          light=(0.3, 0.9), strobe=1, pace="beat", drive="kick", release=0.22,
          upper=_tiers(["wash"], ["wash", "rise"], ["sweep", "rise"]),
          foot=_tiers(["glow"], ["rows"], ["rows", "pulse"]),
          bar=_tiers(["trace", "vu"], ["drumkit", "tiles", "vu", "bounce"], ["drumkit", "mirror", "ripple"]),
          keys="sparkle"),
    Style("pop",
          palettes=(P("#FF5FA2", "#4FC3F7", "#FFE14D"), P("#4FC3F7", "#9C6BFF", "#FF5FA2"),
                    P("#FFE14D", "#FF7A59", "#FF5FA2"), P("#5FFFC8", "#4FC3F7", "#C9A0FF")),
          peak=P("#FFFFFF", "#FF5FA2", "#FFE14D"),
          light=(0.4, 1.0), strobe=1, pace="beat", drive="kick", release=0.22,
          upper=_tiers(["breathe", "ribbon"], ["wash", "sweep"], ["sweep", "chase"]),
          foot=_tiers(["glow"], ["pulse", "chase"], ["chase", "rows"]),
          bar=_tiers(["ribbon", "trace"], ["spectrum", "bounce", "tiles", "counter"], ["mirror", "comet", "sparks"]),
          keys="eq"),
    Style("rock",
          palettes=(P("#FF2000", "#FF9000"), P("#2050FF", "#FFFFFF"), P("#FF9000", "#FFC040"),
                    P("#8000FF", "#FF2000")),
          peak=P("#FFFFFF", "#FF2000"),
          light=(0.3, 1.0), strobe=1, pace="beat", drive="snare", release=0.16,
          upper=_tiers(["wash"], ["slab", "rise"], ["slab", "sweep"]),
          foot=_tiers(["glow"], ["pulse", "rows"], ["pulse"]),
          bar=_tiers(["vu", "fire"], ["drumkit", "vu", "fire"], ["fire", "drumkit", "sparks"]),
          keys="pulse"),
    Style("metal",
          palettes=(P("#FF1000", "#FF8000", "#FFFFFF"), P("#80C0FF", "#FFFFFF", "#6000C0"),
                    P("#FF1000", "#FFC000", "#2040FF")),
          peak=P("#FFFFFF", "#FF1000"),
          light=(0.2, 1.0), strobe=2, pace="half", drive="kick", release=0.12,
          upper=_tiers(["wash"], ["slab"], ["slab", "chase"]),
          foot=_tiers(["glow"], ["pulse"], ["pulse", "rows"]),
          bar=_tiers(["fire"], ["fire", "drumkit"], ["fire", "sparks"]),
          keys="sparkle"),
    Style("oldies",
          palettes=(P("#FFA040", "#D01030", "#40E0B0"), P("#FFA040", "#F0F0FF", "#20B0B0"),
                    P("#D01030", "#FFC040", "#FFA040")),
          peak=P("#FFE0B0", "#FFA040", "#D01030"),
          light=(0.4, 0.85), strobe=0, gap="dim", pace="beat", drive="snare", release=0.35,
          upper=_tiers(["marquee"], ["marquee", "tubes"], ["tubes", "marquee"]),
          foot=_tiers(["glow"], ["chase"], ["chase", "pulse"]),
          bar=_tiers(["vu"], ["vu", "bounce", "tiles"], ["tiles", "counter"]),
          keys="pulse"),
    Style("country",
          palettes=(P("#FFD8A0", "#FFA040", "#C05010"), P("#FF8030", "#E06070", "#6080B0"),
                    P("#FFA040", "#FFC860")),
          peak=P("#FFE8C8", "#FFA040"),
          light=(0.25, 0.75), strobe=0, gap="dim", pace="half", drive="kick", release=0.4,
          upper=_tiers(["breathe"], ["wash", "breathe"], ["wash", "sweep"]),
          foot=_tiers(["glow"], ["glow", "pulse"], ["pulse"]),
          bar=_tiers(["ribbon", "trace"], ["vu", "bounce"], ["vu", "tiles"]),
          keys="pulse"),
    Style("soul",
          palettes=(P("#FFC040", "#FF2020", "#20FF60", "#2060FF"), P("#FF00A0", "#8000FF", "#FF8090"),
                    P("#FFC040", "#FFE8C0", "#FF3000")),
          peak=P("#FFFFFF", "#FFC040", "#FF00A0"),
          light=(0.35, 0.95), strobe=1, pace="beat", drive="kick", release=0.25,
          upper=_tiers(["wash"], ["chase", "wash"], ["chase", "sweep"]),
          foot=_tiers(["glow"], ["checker"], ["checker", "chase"]),
          bar=_tiers(["vu"], ["spectrum", "drumkit", "counter", "classic"], ["spectrum", "sparks", "mirror"]),
          keys="sparkle"),
    Style("jazz",
          palettes=(P("#FFA040", "#A01020"), P("#4070B0", "#302080"), P("#FFA040", "#FFC860")),
          peak=P("#FFC860", "#FFA040"),
          light=(0.2, 0.6), strobe=0, gap="", pace="half", drive="level", release=0.5, beat_locked=False,
          upper=_tiers(["wash"], ["wash", "breathe"], ["wash"]),
          foot=_tiers(["glow"], ["glow"], ["glow"]),
          bar=_tiers(["vu"], ["vu", "trace", "rain"], ["vu", "tiles"]),
          keys="glow"),
    Style("chill",
          palettes=(P("#9080D0", "#5070A0"), P("#FFD890", "#FFB090"), P("#40B0B0", "#3060C0")),
          peak=P("#FFE8C0", "#C0B0FF"),
          light=(0.1, 0.7), strobe=0, gap="", pace="half", drive="level", release=0.6, beat_locked=False,
          upper=_tiers(["breathe"], ["breathe", "ribbon"], ["wash"]),
          foot=_tiers(["glow"], ["glow"], ["glow"]),
          bar=_tiers(["ribbon"], ["ribbon", "trace", "rain"], ["ribbon", "tiles"]),
          keys="glow"),
    Style("latin",
          palettes=(P("#FF2020", "#FFC000", "#20C040"), P("#FF6050", "#FF00A0", "#20E0D0"),
                    P("#FFE020", "#20E0D0", "#FF6050")),
          peak=P("#FFFFFF", "#FFE020", "#FF00A0"),
          light=(0.3, 0.9), strobe=1, pace="beat", drive="offbeat", release=0.3,
          upper=_tiers(["wash"], ["wash", "sweep"], ["sweep", "chase"]),
          foot=_tiers(["glow"], ["offbeat"], ["offbeat", "pulse"]),
          bar=_tiers(["vu"], ["vu", "drumkit", "bounce", "tiles"], ["spectrum", "counter", "sparks"]),
          keys="sparkle"),
]}

# Genre and tag words -> style. The first of a song's tags that matches wins.
GENRE_STYLE = {
    "edm": "edm", "electronic": "edm", "electronica": "edm", "dance": "edm", "house": "edm",
    "techno": "edm", "trance": "edm", "drum and bass": "edm", "electro": "edm", "club": "edm",
    "dubstep": "bass", "bass music": "bass", "brostep": "bass", "trap": "hiphop",
    "hip-hop": "hiphop", "hip hop": "hiphop", "rap": "hiphop", "grime": "hiphop",
    "pop": "pop", "synthpop": "pop", "indie pop": "pop", "k-pop": "pop", "new wave": "pop",
    "synthwave": "pop", "dance-pop": "pop", "electropop": "pop",
    "rock": "rock", "alternative": "rock", "alternative rock": "rock", "indie": "rock",
    "indie rock": "rock", "hard rock": "rock", "grunge": "rock", "punk": "rock", "punk rock": "rock",
    "emo": "rock", "progressive rock": "rock", "psychedelic": "rock",
    "metal": "metal", "heavy metal": "metal", "thrash metal": "metal", "death metal": "metal",
    "black metal": "metal", "metalcore": "metal", "industrial": "metal",
    "classic rock": "oldies", "oldies": "oldies", "rockabilly": "oldies", "soft rock": "oldies",
    "50s": "oldies", "60s": "oldies", "70s": "oldies", "doo-wop": "oldies", "rock and roll": "oldies",
    "country": "country", "folk": "country", "americana": "country", "singer-songwriter": "country",
    "acoustic": "country", "bluegrass": "country",
    "soul": "soul", "funk": "soul", "disco": "soul", "rnb": "soul", "r&b": "soul", "motown": "soul",
    "gospel": "soul", "neo-soul": "soul",
    "jazz": "jazz", "blues": "jazz", "swing": "jazz", "bossa nova": "jazz",
    "classical": "chill", "ambient": "chill", "lo-fi": "chill", "chillout": "chill", "chill": "chill",
    "soundtrack": "chill", "piano": "chill",
    "reggae": "latin", "ska": "latin", "latin": "latin", "reggaeton": "latin", "salsa": "latin",
    "dancehall": "latin", "afrobeat": "latin",
}


def pick(genre, tags, arousal) -> Style:
    """The style for a song: its genre, else the first tag we know, else its mood."""
    for word in ([genre] if genre else []) + list(tags or []):
        name = GENRE_STYLE.get(str(word).lower())
        if name:
            return STYLES[name]
    if arousal is not None and arousal < 0.4:
        return STYLES["chill"]
    return STYLES["pop"]


def seed(title, artist) -> int:
    """The same song always gets the same choices; different songs differ."""
    key = f"{title or ''}|{artist or ''}".encode()
    return int.from_bytes(hashlib.sha1(key).digest()[:4], "big")
