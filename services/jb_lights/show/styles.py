"""Show styles: how the cabinet dresses each kind of music.

A style is what a lighting designer brings to a genre: its palettes, how
hard it hits, whether it strobes, what drives the rhythm, and which
movement each part of the cabinet makes in each kind of moment. The
director (director.py) picks one per song -- from the song's genre and
listeners' tags (jb-analyzer's slow channel), else from its mood -- and the
parts (parts.py) draw it.

Styles are **show files** (docs/contracts/lights.md, Shows): JSON recipes
from the engine's effect library, no code. The built-in ones are in
services/jb_lights/shows/; the owner can upload more, and modules can bring
their own (library.py finds them all). check() is the one validator, used
here and by the admin's upload.

Every part has a job (docs/Light Show Styles.md), named by its role in a
show file:
  storyteller  the upper panel: sections, colour, the build and the drop
  rhythm       the footlights: the kick (or snare, or off-beat) made visible
  meter        the level bar: the music itself, or a second rhythm --
               changing every 8 bars and at each section
  keys         the keypad: hats and accents

Scenes are listed per tier -- calm (verses, breakdowns, quiet songs), main,
peak (choruses, drops) -- as alternatives: each song picks its own, and
they rotate every so many bars so a long song doesn't repeat itself.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "shows"
FORMATS = {1}
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
TIERS = ("calm", "main", "peak")
ROLES = {"storyteller": "upper", "rhythm": "foot", "meter": "bar"}   # show file role -> part
MAX_BYTES = 64 * 1024

# The effect library: the scenes each role can play (parts.py draws them;
# tests check the two agree).
SCENES = {
    "storyteller": {"breathe", "wash", "chase", "sweep", "rise", "slab", "wobble", "marquee", "tubes", "eq",
                    "ribbon", "arena"},
    "rhythm": {"glow", "pulse", "rows", "chase", "checker", "offbeat", "arena"},
    "meter": {"spectrum", "classic", "vu", "ribbon", "drumkit", "tiles", "mirror", "bounce", "trace", "ripple",
              "counter", "comet", "fire", "rain", "sparks", "arena"},
    "keys": {"sparkle", "eq", "pulse", "glow", "arena"},
}


def hexrgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))


def P(*hexes):
    return tuple(hexrgb(h) for h in hexes)


@dataclass(frozen=True)
class Style:
    name: str                       # the show's id
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
    title: str = ""                 # what the owner sees
    description: str = ""
    groups: tuple = ()              # the genre groups it's offered for (GENRE_STYLE's values)
    source: str = "builtin"         # builtin, uploaded, or a module's id
    version: str = ""
    author: str = ""
    inputs: dict = field(default_factory=dict, compare=False, hash=False)   # what modules' lights do to it


# Genre and tag words -> genre group. The first of a song's tags that matches
# wins; each group's shows take turns, song by song (library.py).
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
GROUPS = ("edm", "bass", "hiphop", "pop", "rock", "metal", "oldies", "country", "soul", "jazz", "chill", "latin")
GROUP_NAMES = {"edm": "EDM and dance", "bass": "Dubstep and bass", "hiphop": "Hip-hop and rap", "pop": "Pop",
               "rock": "Rock", "metal": "Metal", "oldies": "Oldies and classic rock",
               "country": "Country and folk", "soul": "Soul, funk and disco", "jazz": "Jazz and blues",
               "chill": "Chill, classical and ambient", "latin": "Reggae and Latin"}
CHILL, LIVELY = "chill", "pop"      # with no genre: calm music and the rest


def group_of(genre, tags, arousal) -> str:
    """A song's genre group: its genre, else the first tag we know (a
    compound tag by its main word: "doom metal" is metal), else its mood."""
    words = [str(w).lower() for w in ([genre] if genre else []) + list(tags or [])]
    for g in [GENRE_STYLE.get(w) for w in words] + [GENRE_STYLE.get(p) for w in words for p in reversed(w.split())]:
        if g:
            return g
    return CHILL if arousal is not None and arousal < 0.4 else LIVELY


def seed(title, artist) -> int:
    """The same song always gets the same choices; different songs differ."""
    key = f"{title or ''}|{artist or ''}".encode()
    return int.from_bytes(hashlib.sha1(key).digest()[:4], "big")


# Show files ------------------------------------------------------------------------

def check(d, source="uploaded") -> list[str]:
    """What's wrong with a show file, in plain words (empty: it's fine)."""
    if not isinstance(d, dict):
        return ["a show file must be a JSON object"]
    p = []
    if d.get("format") not in FORMATS:
        p.append(f"it's show format {d.get('format')!r}; this jukebox knows {sorted(FORMATS)}")
    sid = d.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid) or len(sid) > 40:
        p.append("its id must be lowercase letters, numbers and hyphens (at most 40)")
    for key in ("name", "version"):
        if not isinstance(d.get(key), str) or not d[key].strip() or len(d[key]) > 80:
            p.append(f"it needs a {key} (at most 80 characters)")
    if not isinstance(d.get("description", ""), str) or len(d.get("description", "")) > 400:
        p.append("its description must be text, at most 400 characters")
    groups = d.get("for", [])
    if not isinstance(groups, list) or not set(groups) <= set(GROUPS):
        p.append(f"\"for\" must list genre groups from: {', '.join(GROUPS)}")

    def palette(v, what):
        if not isinstance(v, list) or not 1 <= len(v) <= 8 or not all(isinstance(c, str) and HEX_RE.match(c) for c in v):
            p.append(f"{what} must be 1 to 8 colours like \"#FF00C8\"")
    pals = d.get("palettes")
    if not isinstance(pals, list) or not 1 <= len(pals) <= 8:
        p.append("palettes must be a list of 1 to 8 palettes")
    else:
        for k, v in enumerate(pals):
            palette(v, f"palette {k + 1}")
    palette(d.get("peak"), "peak")
    light = d.get("light", [0.3, 1.0])
    if not (isinstance(light, list) and len(light) == 2 and all(isinstance(x, (int, float)) for x in light)
            and 0 <= light[0] <= light[1] <= 1):
        p.append("light must be [floor, ceiling], 0 to 1, floor no higher than ceiling")
    for key, ok in (("strobe", (0, 1, 2)), ("gap", ("blackout", "dim", "")), ("pace", ("half", "beat", "double")),
                    ("drive", ("kick", "snare", "offbeat", "level"))):
        if key in d and d[key] not in ok:
            p.append(f"{key} must be one of {', '.join(repr(x) for x in ok)}")
    rel = d.get("release", 0.2)
    if not isinstance(rel, (int, float)) or not 0.05 <= rel <= 2:
        p.append("release must be 0.05 to 2 seconds")
    if not isinstance(d.get("beat_locked", True), bool):
        p.append("beat_locked must be true or false")
    roles = d.get("roles")
    if not isinstance(roles, dict):
        p.append("it needs roles: storyteller, rhythm, meter and keys")
    else:
        for role in ROLES:
            tiers = roles.get(role)
            if not isinstance(tiers, dict) or set(tiers) != set(TIERS):
                p.append(f"roles.{role} must give calm, main and peak")
                continue
            for tier in TIERS:
                opts = tiers[tier]
                if not isinstance(opts, list) or not 1 <= len(opts) <= 12:
                    p.append(f"roles.{role}.{tier} must list 1 to 12 scenes")
                elif not all(o in SCENES[role] for o in opts):
                    bad = [o for o in opts if o not in SCENES[role]]
                    p.append(f"roles.{role}.{tier}: no scene {', '.join(map(repr, bad))} "
                             f"(the {role} can play {', '.join(sorted(SCENES[role]))})")
        if roles.get("keys", "sparkle") not in SCENES["keys"]:
            p.append(f"roles.keys must be one of {', '.join(sorted(SCENES['keys']))}")
        extra = set(roles) - set(ROLES) - {"keys"}
        if extra:
            p.append(f"unknown roles: {', '.join(sorted(extra))}")
    inputs = d.get("inputs", {})
    if not isinstance(inputs, dict):
        p.append("inputs must be an object")
    else:
        from services.jb_lights.show import layers         # the cue effects live with the layers
        tint = inputs.get("palette", 0.5)
        if not isinstance(tint, (int, float)) or not 0 <= tint <= 1:
            p.append("inputs.palette must be 0 to 1 (how far a module's colours tint this show)")
        cues = inputs.get("cues", {})
        if not isinstance(cues, dict) or not all(isinstance(k, str) and v in layers.CUE_EFFECTS for k, v in cues.items()):
            p.append(f"inputs.cues maps cue names to effects: {', '.join(sorted(layers.CUE_EFFECTS))}")
    return p


def from_json(d, source="builtin") -> Style:
    """A checked show file as a Style."""
    roles = d["roles"]
    tiers = {part: {t: tuple(roles[role][t]) for t in TIERS} for role, part in ROLES.items()}
    return Style(name=d["id"], palettes=tuple(P(*pal) for pal in d["palettes"]), peak=P(*d["peak"]),
                 light=tuple(d.get("light", (0.3, 1.0))), strobe=d.get("strobe", 1), gap=d.get("gap", "blackout"),
                 pace=d.get("pace", "beat"), drive=d.get("drive", "kick"), release=float(d.get("release", 0.2)),
                 beat_locked=d.get("beat_locked", True), keys=roles.get("keys", "sparkle"),
                 title=d["name"], description=d.get("description", ""), groups=tuple(d.get("for", ())),
                 source=source, version=d["version"], author=str(d.get("author", ""))[:80],
                 inputs=d.get("inputs", {}), **tiers)


def load(path: Path, source="uploaded") -> tuple[Style | None, list[str]]:
    """(the style, []) from a show file, or (None, what's wrong)."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None, [f"{path.name} is over {MAX_BYTES // 1024} KB"]
        d = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return None, [f"{path.name}: {e}"]
    problems = check(d, source)
    return (None, problems) if problems else (from_json(d, source), [])


def _builtin() -> dict:
    out = {}
    for f in sorted(BUILTIN_DIR.glob("*.json")):
        style, problems = load(f, "builtin")
        if problems:
            raise ValueError(f"built-in show {f.name}: {'; '.join(problems)}")
        out[style.name] = style
    return out


STYLES = _builtin()     # the built-in shows, by id
