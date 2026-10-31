"""Song memory: what the analyzer heard of a song, kept so it can study the
whole song once it's over (study.py), and the song maps it has learned.

  Recording  one song while it plays: ten times a second its texture (chroma,
             timbre, loudness, the drums, the cymbals, the stereo picture),
             thirty times a second its onset envelope and drums (for the beats),
             about once a second the ears, the moments as they happened, and the
             source's song position whenever it says
  Store      the songs folder (/var/lib/juk3-box/songs): per song, by key, the
             recording of its last whole play (.npz) and its map (.json)

A recording is only worth studying when it heard the song through: from near
its start, without a seek or a long pause, most of the way to its end
(Recording.coverage). Pure but for Store's files.
"""

import hashlib
import json
import re
import time
from pathlib import Path

import numpy as np

from shared.files import write_atomic

SONGS_DIR = Path("/var/lib/juk3-box/songs")
FORMAT = 1
MIN_COVERAGE = 0.85          # of the song's length, heard in one go
MAX_START_S = 8.0            # the recording must reach back to within this of the start
MIN_SECONDS = 45.0
JUMP_S = 1.5                 # the source's position this far off the clock: a seek or a pause
ROW = ("rms_db", "bass_db", "level", "brightness", "width", "centre", "ring", "onset", "kick", "snare", "hat",
       "crash", "beats", "sounding")
FAST = ("flux", "kick", "snare", "rms_db")
EARS = ("music", "speech", "voice", "rap", "drums", "cymbal", "guitar", "synth", "keys", "strings", "brass",
        "happy", "sad", "tender", "exciting", "angry", "scary")


def song_key(artist, title, duration_s=None) -> str | None:
    """The same song (by name and, when known, length to ~3 s) gets the same key."""
    def norm(s):
        return re.sub(r"\s+", " ", str(s or "").strip().lower())
    a, t = norm(artist), norm(title)
    if not t:
        return None
    d = "" if not duration_s else str(int(round(float(duration_s) / 3)))
    return hashlib.sha1(f"{a}|{t}|{d}".encode()).hexdigest()[:16]


class Recording:
    def __init__(self, identity: dict, fps: float, started: float | None = None):
        self.identity = dict(identity)
        self.fps = fps                       # the fast layer's frames a second
        self.started = time.monotonic() if started is None else started
        self.t, self.chroma, self.mfcc, self.rows = [], [], [], []
        self.fast_t, self.fast = [], []
        self.ears_t, self.ears, self.genres = [], [], []
        self.events = []
        self.anchors = []                    # (t, song position) from the source
        self._acc = None

    # While it plays -----------------------------------------------------------------

    def frame(self, t: float, f: dict, sounding: bool):
        """Every fast frame (the fast layer's output, internals included)."""
        self.fast_t.append(t)
        self.fast.append((f["_flux"], f["kick"], f["snare"], f["_rms_db"]))
        a = self._acc
        if a is None:
            a = self._acc = {"onset": 0.0, "kick": 0.0, "snare": 0.0, "hat": 0.0, "crash": 0.0, "beats": 0,
                             "ring": 0.0, "level": 0.0, "brightness": 0.0, "bass_db": 0.0, "n": 0, "sounding": 0}
        a["onset"] += f["onset"]
        for k in ("kick", "snare", "hat", "crash"):
            a[k] = max(a[k], f[k])
        a["beats"] += 1 if f["beat"] else 0
        a["ring"] += f["ring"]
        a["level"] += f["level"]
        a["brightness"] += f["brightness"]
        a["bass_db"] += f["_bass_db"]
        a["n"] += 1
        a["sounding"] += 1 if sounding else 0

    def texture(self, t: float, tex: dict, rms_db: float):
        """Every texture frame (texture.py), after the fast frames it covers."""
        a, self._acc = self._acc or {"n": 0}, None
        n = max(1, a.get("n", 0))
        self.t.append(t)
        self.chroma.append(tex["chroma"])
        self.mfcc.append(tex["mfcc"])
        self.rows.append((rms_db, a.get("bass_db", -120.0) / n, a.get("level", 0.0) / n,
                          a.get("brightness", 0.0) / n, tex["width"], tex["centre"], a.get("ring", 0.0) / n,
                          a.get("onset", 0.0) / n, a.get("kick", 0.0), a.get("snare", 0.0), a.get("hat", 0.0),
                          a.get("crash", 0.0), a.get("beats", 0), a.get("sounding", 0) / n))

    def heard(self, t: float, groups: dict):
        """The ears' groups (ears.hear) for the window that ended at t."""
        self.ears_t.append(t)
        moods = groups.get("moods", {})
        self.ears.append(tuple(groups.get(k, moods.get(k, 0.0)) for k in EARS))
        self.genres.append(dict(groups.get("genres", {})))

    def event(self, t: float, e: dict):
        self.events.append({"t": round(t, 3), **e})

    def position(self, t: float, pos_s: float | None):
        """The source's song position, as it said it at t."""
        if pos_s is not None:
            self.anchors.append((t, float(pos_s)))

    # After -----------------------------------------------------------------------

    def offset(self):
        """(song position - clock) over the longest stretch the source's
        position followed the clock, as (offset, from t, to t), or None."""
        if not self.anchors:
            return None
        runs, run = [], [self.anchors[0]]
        for a in self.anchors[1:]:
            prev = run[-1]
            if abs((a[1] - prev[1]) - (a[0] - prev[0])) > JUMP_S:
                runs.append(run)
                run = [a]
            else:
                run.append(a)
        runs.append(run)
        best = max(runs, key=lambda r: r[-1][0] - r[0][0])
        off = float(np.median([p - t for t, p in best]))
        start, end = best[0][0], best[-1][0]
        if best is runs[-1] and self.t:
            end = max(end, self.t[-1])                 # the last stretch runs on to the end of the recording
        if best is runs[0] and self.t:
            start = min(start, self.t[0])
        return off, start, end

    def coverage(self, duration_s=None) -> float:
        """How much of the song this recording heard in one go (0..1)."""
        o = self.offset()
        dur = duration_s or self.identity.get("duration")
        if not self.t:
            return 0.0
        if o is None:
            return 0.0                                 # no position: can't place it in the song
        off, start, end = o
        t = np.array(self.t)
        inside = t[(t >= start) & (t <= end)]
        if inside.size == 0:
            return 0.0
        first, last = inside[0] + off, inside[-1] + off
        if first > MAX_START_S or last - max(first, 0) < MIN_SECONDS:
            return 0.0
        if not dur:
            return 1.0
        return float(np.clip((min(last, dur) - max(first, 0)) / dur, 0, 1))

    def arrays(self) -> dict:
        """Everything, with song positions, ready to save or study."""
        o = self.offset()
        off, start, end = o if o else (-(self.t[0] if self.t else 0.0), -1e18, 1e18)
        def keep(ts):
            ts = np.asarray(ts, dtype=float)
            return (ts >= start - 1) & (ts <= end + 1)
        t = np.asarray(self.t, dtype=float)
        k = keep(t)
        ft = np.asarray(self.fast_t, dtype=float)
        fk = keep(ft)
        et = np.asarray(self.ears_t, dtype=float)
        ek = keep(et)
        return {
            "format": np.array(FORMAT),
            "fps": np.array(self.fps),
            "pos": (t[k] + off).astype(np.float32),
            "chroma": np.asarray(self.chroma, dtype=np.float16).reshape(-1, 12)[k],
            "mfcc": np.asarray(self.mfcc, dtype=np.float32).reshape(-1, 13)[k],
            "rows": np.asarray(self.rows, dtype=np.float32).reshape(-1, len(ROW))[k],
            "fast_pos": (ft[fk] + off).astype(np.float64),
            "fast": np.asarray(self.fast, dtype=np.float32).reshape(-1, len(FAST))[fk],
            "ears_pos": (et[ek] + off).astype(np.float32),
            "ears": np.asarray(self.ears, dtype=np.float32).reshape(-1, len(EARS))[ek],
            "genres": np.array(json.dumps([g for g, ok in zip(self.genres, ek) if ok])),
            "events": np.array(json.dumps([{**e, "t": round(e["t"] + off, 3)} for e in self.events
                                           if start - 1 <= e["t"] <= end + 1])),
            "identity": np.array(json.dumps(self.identity)),
        }


def load_arrays(path) -> dict:
    with np.load(path) as z:
        d = {k: z[k] for k in z.files}
    for k in ("genres", "events", "identity"):
        d[k] = json.loads(str(d[k]))
    d["fps"] = float(d["fps"])
    return d


class Store:
    """The songs folder: <key>.npz (the last whole play) and <key>.json (its map)."""

    def __init__(self, folder: Path = SONGS_DIR):
        self.dir = Path(folder)

    def map_path(self, key):
        return self.dir / f"{key}.json"

    def recording_path(self, key):
        return self.dir / f"{key}.npz"

    def save_recording(self, key, arrays: dict) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.recording_path(key)
        tmp = path.with_name(f".{key}.tmp.npz")
        np.savez_compressed(tmp, **arrays)
        tmp.replace(path)
        return path

    def save_map(self, key, song_map: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.map_path(key), json.dumps(song_map, separators=(",", ":")) + "\n")

    def load_map(self, key) -> dict | None:
        if not key:
            return None
        try:
            d = json.loads(self.map_path(key).read_text())
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) and d.get("format") == FORMAT else None

    def forget(self, key):
        for p in (self.map_path(key), self.recording_path(key)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def songs(self) -> list:
        """Every learned song's summary, newest first."""
        out = []
        for p in self.dir.glob("*.json") if self.dir.is_dir() else []:
            try:
                d = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and d.get("format") == FORMAT:
                out.append({"key": p.stem, **{k: d.get(k) for k in ("song", "plays", "learned", "tempo", "feel")},
                            "sections": [s.get("role") for s in d.get("sections", [])]})
        return sorted(out, key=lambda s: s.get("learned") or "", reverse=True)
