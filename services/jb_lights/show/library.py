"""Every show the cabinet can play, and which one a song gets.

  built-in   services/jb_lights/shows/*.json
  modules    an installed module's shows (its manifest's "shows"), named
             after the module like its blocks (jb-weather: "weather",
             "weather-…")
  uploaded   /var/lib/juk3-box/lights/shows/*.json (admin › Light Show)

Which show a song gets (choose()): a pinned show, for every song; else,
once the analyzer has said what kind of song it is (its feel), the show
that fits it best (plan.fit: the feel against each show's "fits", its
decade and tags, its genre group) -- of every show, or of the owner's
rotation when they made one for the song's genre group. Before the feel,
the genre group's rotation -- the owner's list for it, or every show made
"for" it (its own built-in first) -- with the song's seed picking one
(pick()). Shows that fit about as well take turns by the seed, so a song
always gets the same show. Disabled shows are never picked. Changes on disk
are picked up within REFRESH_S, without a restart.
"""

import json
import time
from pathlib import Path

from services.jb_lights import owner
from services.jb_lights.show import plan, styles

UPLOADED_DIR = owner.DIR / "shows"
MODULES_DIR = Path("/var/lib/juk3-box/modules")
REFRESH_S = 2.0


def module_prefix(module_id: str) -> str:
    return module_id[3:] if module_id.startswith("jb-") else module_id


def module_show_ok(module_id: str, show_id: str) -> bool:
    p = module_prefix(module_id)
    return show_id == p or show_id.startswith(p + "-")


def scan_modules(modules_dir: Path) -> tuple[dict, list[str]]:
    """{show id: Style} from installed modules' manifests, and what was skipped."""
    found, notes = {}, []
    for manifest in sorted(Path(modules_dir).glob("*/module.json")):
        try:
            m = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        mid = m.get("id") if isinstance(m, dict) else None
        if not isinstance(mid, str):
            continue
        for rel in m.get("shows", []) or []:
            path = (manifest.parent / str(rel)).resolve()
            if manifest.parent.resolve() not in path.parents:
                notes.append(f"{mid}: {rel} is outside the module")
                continue
            style, problems = styles.load(path, mid)
            if problems:
                notes.append(f"{mid}: {'; '.join(problems)}")
            elif not module_show_ok(mid, style.name):
                notes.append(f"{mid}: show {style.name!r} must be named {module_prefix(mid)!r} or start with it")
            else:
                found[style.name] = style
    return found, notes


def scan_uploaded(shows_dir: Path) -> tuple[dict, list[str]]:
    found, notes = {}, []
    for f in sorted(Path(shows_dir).glob("*.json")):
        style, problems = styles.load(f, "uploaded")
        if problems:
            notes.append(f"{f.name}: {'; '.join(problems)}")
        else:
            found[style.name] = style
    return found, notes


class Library:
    """settings: a fixed settings dict (tests, previews); else the owner's file."""

    def __init__(self, settings=None, settings_path=owner.PATH, shows_dir=UPLOADED_DIR, modules_dir=MODULES_DIR):
        self.fixed = owner.clean(settings) if settings is not None else None
        self.settings_path, self.shows_dir, self.modules_dir = settings_path, shows_dir, modules_dir
        self.settings = self.fixed or owner.clean({})
        self.shows = dict(styles.STYLES)
        self.notes: list[str] = []
        self._stamp = None
        self._checked = -1e9
        self.refresh(force=True)

    def _signature(self):
        def mtimes(paths):
            out = []
            for p in paths:
                try:
                    out.append((str(p), p.stat().st_mtime_ns))
                except OSError:
                    pass
            return out
        paths = [Path(self.settings_path), Path(self.shows_dir)]
        paths += list(Path(self.shows_dir).glob("*.json")) if Path(self.shows_dir).is_dir() else []
        paths += list(Path(self.modules_dir).glob("*/module.json")) if Path(self.modules_dir).is_dir() else []
        return tuple(mtimes(paths))

    def refresh(self, force=False, now=None):
        now = time.monotonic() if now is None else now
        if not force and now - self._checked < REFRESH_S:
            return
        self._checked = now
        stamp = self._signature() if self.fixed is None or force else self._stamp
        if not force and stamp == self._stamp:
            return
        self._stamp = stamp
        if self.fixed is None:
            self.settings = owner.read(self.settings_path)
        shows, notes = dict(styles.STYLES), []
        mod, n1 = scan_modules(self.modules_dir) if self.fixed is None else ({}, [])
        up, n2 = scan_uploaded(self.shows_dir) if self.fixed is None else ({}, [])
        for source in (mod, up):                      # built-ins win, then modules, then uploads
            for sid, st in source.items():
                if sid in shows:
                    notes.append(f"{sid}: a show with that id is already there")
                else:
                    shows[sid] = st
        self.shows, self.notes = shows, n1 + n2 + notes

    def rotation(self, group: str) -> list[str]:
        """The shows that take turns for a genre group, in order."""
        off = set(self.settings["disabled"])
        if group in self.settings["rotations"]:
            ids = self.settings["rotations"][group]
        else:
            ids = sorted((sid for sid, st in self.shows.items() if group in st.groups),
                         key=lambda sid: (sid != group, self.shows[sid].source != "builtin", sid))
        ids = [i for i in ids if i in self.shows and i not in off]
        return ids or ([group] if group in styles.STYLES else [styles.LIVELY])

    def idle_show(self):
        """The show the owner chose for no music (settings "idle"), or None
        for the city and the waves."""
        sid = self.settings["idle"]
        if sid != "city" and sid in self.shows and sid not in self.settings["disabled"]:
            return self.shows[sid]
        return None

    def choose(self, feel, genre, tags, arousal, seed=0) -> tuple:
        """(the show for a song, why: the words it was chosen by)."""
        self.refresh()
        s = self.settings
        group = styles.group_of(genre, tags, arousal)
        pin = s["pin"]
        if pin in self.shows and pin not in s["disabled"]:
            return self.shows[pin], plan.why(feel, None, pinned=True)
        named = styles.genre_group(genre, tags)     # only a genre the song has counts toward the fit
        if not feel:
            return self.pick(genre, tags, arousal, seed), plan.why(None, named)
        if group in s["rotations"]:
            ids = self.rotation(group)
        else:
            # every show made for songs (one naming no genre and no feel is an idle show, like Weather's)
            ids = [sid for sid, st in self.shows.items()
                   if sid not in s["disabled"] and (st.groups or st.fits)] or [styles.LIVELY]
        return self.shows[plan.best(self.shows, ids, feel, named, tags, seed)], plan.why(feel, named)

    def pick(self, genre, tags, arousal, seed=0) -> styles.Style:
        self.refresh()
        pin = self.settings["pin"]
        if pin in self.shows and pin not in self.settings["disabled"]:
            return self.shows[pin]
        turns = self.rotation(styles.group_of(genre, tags, arousal))
        return self.shows[turns[seed % len(turns)]]
