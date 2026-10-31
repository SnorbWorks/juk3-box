"""The choreographer: one plan per song, the way a lighting designer plans
a show before the gig, from what jb-analyzer says about the song (its feel,
its sections, its map: docs/contracts/audio-analysis.md). The whole song
gets one choreographed feel, and each part of it a look that comes back
when the music does.

  the show   the one that fits the song best (fit(): its feel against each
             show's "fits", its decade and tags, the genre group its genre
             or tags name), chosen by library.choose(); a pinned show is the
             look whatever the song. With no feel yet, the genre's rotation
  colours    one family for the whole song: the show's palette whose warmth
             suits the feel (a moody song gets the bluest, a love song the
             warmest). Sections vary it: a new lead colour per label, darker
             for the quiet ones, the show's peak colours for the song's peak,
             its last chorus and a drop
  motion     one rate -- movement steps a beat: a ballad moves once a bar,
             most songs once a beat, a fast hard one twice -- and one
             smoothness (long fades, soft pulses, slow crossfades for a
             tender or a slow song; punchy for a hard one)
  accents    what the big moments do, by the show and the feel: crash
             cymbals splash (a ballad's only shimmer), a drop or the hit
             after a gap slams in or flashes white (a love song's swells),
             gaps go dark
  looks      per section, by its character (character()): quiet, vocal (the
             EQ look), drive, big (the rock-show moment), build, break, solo.
             The same label always gets the same look (the chorus again
             looks like the chorus), and grows a little each time round

Pure: Plan(show, feel, tempo, seed) -> Plan.look(section) -> Look.
"""

from dataclasses import dataclass, field

from services.jb_lights.show import styles

PARTS = ("upper", "foot", "bar")
# How bright each character sits in the show's light range (0 its floor .. 1 its ceiling).
CHARACTER_GAIN = {"break": 0.35, "quiet": 0.45, "vocal": 0.68, "build": 0.72, "drive": 0.82, "solo": 0.92,
                  "big": 1.0}
BIGGER = ("break", "quiet", "vocal", "build", "drive", "solo", "big")   # small to big
GROW = 0.06                  # each time round again, a section's look this much brighter
VOICED = 0.4                 # a section with this much voice (0..1 within the song) is sung
# A typical song's feel (jb-analyzer's, for a song with no tags): what a show
# naming nothing about an axis is taken to be made for.
TYPICAL = {"energy": 0.5, "valence": 0.5, "tender": 0.1, "aggressive": 0.2, "groove": 0.3, "moody": 0.3,
           "pace": 0.5, "voice": 0.5}
FIT_AXIS = 2.0               # a song's feel off a show's targets costs this much (the squares, averaged)...
SALIENT = 4.0                # ...each axis the more, the further the song is from typical on it
FIT_GROUP = 0.3              # a show made for the song's genre group
FIT_TAG = 0.3                # ...naming one of its listeners' tags
FIT_ERA = 0.2                # ...for its decade
CLOSE = 0.05                 # shows fitting this close to the best take turns, song by song
WHITE = (255, 255, 255)


def _clip(x, lo=0.0, hi=1.0) -> float:
    return max(lo, min(hi, float(x)))


def _num(v, default):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def lerp(a, b, k):
    return tuple(x + (y - x) * k for x, y in zip(a, b))


def blend(p, q, k) -> tuple:
    """Two palettes mixed, colour by colour (the shorter one going round)."""
    n = max(len(p), len(q))
    return tuple(lerp(p[i % len(p)], q[i % len(q)], k) for i in range(n))


def warmth(palette) -> float:
    """How warm a palette reads: -1 (blues) .. 1 (reds, oranges, pinks)."""
    return sum((c[0] - c[2]) / 255 for c in palette) / max(1, len(palette))


# Choosing the show -------------------------------------------------------------

def fit(show, feel: dict, group: str | None, tags=()) -> float:
    """How well a show suits a song (higher is better; 0 is a fair fit). What
    sets a song apart counts most: a very moody song is matched on its
    moodiness first, whatever its tags say."""
    f = show.fits
    off = 0.0
    for a in styles.FEEL_AXES:
        x = feel.get(a)
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            off += (1 + SALIENT * abs(x - TYPICAL[a])) * (x - f.get(a, TYPICAL[a])) ** 2
    score = -FIT_AXIS * off / len(styles.FEEL_AXES)
    if group and group in show.groups:
        score += FIT_GROUP
    if feel.get("era") and feel["era"] in f.get("eras", ()):
        score += FIT_ERA
    if f.get("tags") and {str(t).lower() for t in tags or ()} & {t.lower() for t in f["tags"]}:
        score += FIT_TAG
    return score


def best(shows: dict, ids, feel, group, tags, seed) -> str:
    """Of the shows ids, the one that fits; among close ones, the song's seed picks."""
    scored = [(fit(shows[i], feel, group, tags), i) for i in ids]
    top = max(s for s, _ in scored)
    close = sorted(i for s, i in scored if s >= top - CLOSE)
    return close[seed % len(close)]


def why(feel: dict | None, group: str | None, pinned=False) -> list:
    """What the owner sees: the words the analyzer had for the song."""
    words = list((feel or {}).get("words") or [])
    if group:
        words.append(styles.GROUP_NAMES.get(group, group).lower())
    return (["pinned"] if pinned else []) + words


# The song's parts ------------------------------------------------------------------

def character(s: dict | None) -> str:
    """A section's character, from what the analyzer said about it (its
    role, how big it is, its voice and drums)."""
    if not s:
        return "drive"
    role = s.get("role") or ""
    size = _num(s.get("size", s.get("energy")), 0.5)
    voice = _num(s.get("voice"), 0.0)
    drums = _num(s.get("drums"), 0.5)
    if role == "drop":
        return "big"
    if role in ("build", "pre-chorus"):
        return "build"
    if role == "solo":
        return "solo"
    if role in ("breakdown", "interlude"):
        return "break" if size < 0.45 or role == "breakdown" else "drive"
    if s.get("peak") or (role == "chorus" and (size >= 0.5 or drums >= 0.5 or s.get("last"))):
        return "big"
    if size < 0.25 or (role in ("intro", "outro") and size < 0.45):
        return "quiet"
    if voice >= VOICED:
        return "vocal"
    if drums < 0.3 and size < 0.5:
        return "quiet"
    return "drive"


def rate_of(pace: float, tempo: float | None, bias: str = "auto") -> float:
    """Movement steps a beat: once a bar for a ballad, once a beat for most
    songs, twice for a fast hard one -- half time above 140 bpm, and never
    more than about three steps a second."""
    r = 0.25 if pace < 0.2 else 0.5 if pace < 0.4 else 1.0 if pace < 0.85 else 2.0
    r *= {"auto": 1.0, "half": 0.5, "beat": 1.0, "double": 2.0}.get(bias, 1.0)
    if tempo:
        if tempo > 140 and r >= 1:
            r /= 2
        r = min(r, 3.0 * 60 / tempo)
    return max(0.25, r)


@dataclass
class Look:
    character: str
    label: str | None
    scene: dict              # upper, foot, bar -> a scene; keys -> the keypad's
    palette: tuple
    gain: float              # where in the show's light range (0 floor .. 1 ceiling)
    n: int = 1               # which time round
    peak: bool = False       # the song's peak, its last chorus, a drop: the show's peak colours
    index: int = 0           # its place among the looks of its character

    @property
    def tier(self) -> str:
        return "peak" if self.peak or self.character == "big" else styles.CHARACTER_TIER[self.character]


@dataclass
class Plan:
    show: styles.Style
    feel: dict = field(default_factory=dict)
    tempo: float | None = None
    seed: int = 0
    reasons: list = field(default_factory=list)

    def __post_init__(self):
        f, st = self.feel or {}, self.show
        pace = f.get("pace")
        if not isinstance(pace, (int, float)):
            pace = 0.5 if not self.tempo else _clip((self.tempo - 60) / 90)
        tender, moody = _num(f.get("tender"), 0.0), _num(f.get("moody"), 0.0)
        aggressive = _num(f.get("aggressive"), 0.0)
        smooth = _clip(0.75 * (1 - pace) + 0.4 * tender + 0.2 * moody - 0.45 * aggressive)
        want = st.motion.get("smooth", "auto")
        self.smooth = smooth if want == "auto" else 0.5 * float(want) + 0.5 * smooth
        self.pace = pace
        self.rate = rate_of(pace, self.tempo, st.motion.get("pace", "auto"))
        self.release = _clip(st.release * (0.6 + 1.6 * self.smooth), 0.08, 1.2)
        self.crossfade = 0.35 + 2.4 * self.smooth          # seconds between one look and the next
        gentle = tender >= 0.55 or self.smooth >= 0.75
        self.gentle = gentle
        acc = dict(st.accents)
        if gentle:
            # a ballad swells; it never slams or flashes (the arena keeps its burst: it's the show)
            acc["drop"] = "swell" if acc["drop"] in ("slam", "flash") else acc["drop"]
            acc["crash"] = "shimmer" if acc["crash"] in ("splash", "flash") else acc["crash"]
        self.accents = acc
        self.flash = st.strobe > 0 and not gentle            # white flashes at all
        self.drive = "level" if self.smooth >= 0.75 else st.drive
        self.beat_locked = st.beat_locked and self.smooth < 0.85
        self.palette = self._family()
        self.looks = {}          # label -> Look
        self.order = {}          # character -> labels, in the order their looks were made

    def _family(self) -> tuple:
        """The song's colour family: the show's palette whose warmth suits the
        feel -- the bluest for a moody song, the warmest for a love song. The
        less the feel leans either way, the more of them the song's seed
        picks from (any of them, for a song that leans neither way)."""
        pals = self.show.palettes
        f = self.feel or {}
        if not f:
            return pals[self.seed % len(pals)]
        target = _clip((_num(f.get("valence"), 0.5) - 0.5) * 1.4
                       + 0.8 * max(0.0, _num(f.get("tender"), 0.0) - TYPICAL["tender"])
                       - 1.2 * max(0.0, _num(f.get("moody"), 0.0) - TYPICAL["moody"])
                       + 0.6 * max(0.0, _num(f.get("aggressive"), 0.0) - TYPICAL["aggressive"]), -1, 1)
        ranked = sorted(range(len(pals)), key=lambda k: (abs(warmth(pals[k]) - target), k))
        keep = max(1, round(len(pals) * (1 - abs(target))))
        return pals[ranked[self.seed % keep]]

    def _index(self, ch, label) -> int:
        seen = self.order.setdefault(ch, [])
        if label not in seen:
            seen.append(label)
        return seen.index(label)

    def _palette(self, ch, k, peak, n) -> tuple:
        base = self.palette
        if peak:
            return self.show.peak
        m = k % len(base)
        lead = base[m:] + base[:m]                      # a new lead colour for each label
        if ch in ("quiet", "break"):
            dark = min(base, key=sum)
            return tuple(lerp(c, dark, 0.3) for c in lead)
        if ch == "big":
            lifted = tuple(lerp(c, WHITE, 0.1) for c in lead)
            return blend(lifted, self.show.peak, 0.5) if n >= 3 else lifted    # the third chorus nears the peak
        return lead

    def look(self, s: dict | None = None, ch: str | None = None) -> Look:
        """The look for a section (a section event, or a map's section); or,
        with no section, for a character (ch)."""
        s = s or {}
        label = s.get("label")
        n = max(1, int(_num(s.get("n"), 1)))
        ch = ch or character(s if s else None)
        old = self.looks.get(label) if label else None
        if old is not None and BIGGER.index(ch) <= BIGGER.index(old.character):
            ch, k, scene = old.character, old.index, old.scene       # the same music: the same look
        else:
            k = self._index(ch, label)
            scene = {}
            for j, part in enumerate(PARTS):
                opts = self.show.scenes_for(part, ch)
                scene[part] = opts[(self.seed // (j + 1) + k) % len(opts)]
            scene["keys"] = self.show.scenes_for("keys", ch)[0]
        peak = bool(s.get("peak") or s.get("last") or s.get("role") == "drop")
        gain = min(1.0, CHARACTER_GAIN[ch] * (1 + GROW * (n - 1)) + (0.1 if peak else 0.0))
        look = Look(ch, label, scene, self._palette(ch, k, peak, n), gain, n, peak, k)
        if label:
            self.looks[label] = look
        return look

    def summary(self) -> dict:
        """For admin › Light Show › Now."""
        return {"show": self.show.name, "why": self.reasons, "rate": round(self.rate, 2), "smooth": round(self.smooth, 2),
                "accents": self.accents, "palette": ["#%02x%02x%02x" % tuple(int(v) for v in c) for c in self.palette]}
