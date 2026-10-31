"""The study: a whole song's recording (memory.py) in, its map out.

What a lighting designer works out listening to a song before the gig:

  beats     the tempo, every beat, and which beats start a bar (downbeats)
  sections  where the song changes -- on bar lines, in phrases of 4, 8 or 16
            bars -- which sections are the same music coming back (labels A,
            B, C ...), and what each one is: intro, verse, pre-chorus, chorus,
            bridge, solo, breakdown, build, drop, outro
  energy    how big each section is within the song (0..1), its voice and drums
  moments   every gap, hit, drop, crash and build, at the song's own times
  feel      what kind of song it is (feel.py), from the whole of it

How: beats by dynamic programming over the onset envelope (Ellis 2007, "Beat
tracking by dynamic programming"); downbeats where the kicks land and the
chords change; each bar's harmony (chroma, beat by beat), timbre (MFCC) and
energy; a bar-by-bar self-similarity matrix; a checkerboard novelty curve
(Foote 2000) for where it changes; the boundaries chosen by dynamic
programming that rewards homogeneous sections in whole phrases; sections
clustered by how alike their music is (harmony first); roles from
repetition, energy, voice and place in the song.

Pure numpy, a second or two for a 4-minute song:
    python -m services.jb_analyzer.study recording.npz map.json
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

from services.jb_analyzer.feel import feel as feel_of
from services.jb_analyzer.memory import EARS, FAST, FORMAT, ROW, load_arrays
from services.jb_analyzer.texture import Key
from shared.files import write_atomic

BPM_LO, BPM_HI, BPM_PRIOR = 60.0, 180.0, 120.0
TIGHTNESS = 100.0            # how hard the beat tracker holds the tempo (Ellis' default)
BEATS_PER_BAR = 4
NOVELTY_BARS = 4             # the checkerboard's half-width
MIN_BARS, MAX_BARS = 2, 32   # a section's length
PHRASE = {2: 0.0, 4: 0.5, 6: -0.2, 8: 1.0, 12: 0.4, 16: 0.9, 24: 0.3, 32: 0.3}
ODD_PHRASE = -0.6            # any other length
SECTION_COST = 1.2           # per section, so it doesn't cut everything up
NOVELTY_WEIGHT = 2.0
SAME_Z = 0.55                # two sections this far above the song's typical pair are the same music...
SAME_HARM = 0.84             # ...if their harmony (0..1, bar by bar) is at least this alike
VOICE_APART = 0.25           # sections whose voice differs by more than this aren't the same (a verse, its break)
TIMBRE_APART = -0.3          # nor are sections whose timbre is this far below the song's typical pair
VOICED = 0.4                 # a section with this much voice (0..1, within the song) is sung
ROLES = ("intro", "verse", "pre-chorus", "chorus", "bridge", "solo", "breakdown", "build", "drop", "outro",
         "interlude")


# Beats -------------------------------------------------------------------------

def onset_envelope(fast: np.ndarray) -> np.ndarray:
    """The beat tracker's input: spectral flux and the kick, with the slow swell taken out."""
    flux = np.log1p(np.maximum(fast[:, FAST.index("flux")], 0))
    kick = fast[:, FAST.index("kick")]
    def z(x):
        return (x - x.mean()) / (x.std() + 1e-9)
    o = z(flux) + 0.5 * z(kick)
    k = 15
    o = o - np.convolve(o, np.ones(k) / k, mode="same")
    return np.maximum(o, 0)


def tempo_of(o: np.ndarray, fps: float, hint: float | None = None) -> tuple:
    """(bpm, confidence 0..1) from the whole song's onset autocorrelation,
    weighed toward ~120 bpm (and toward the live tempo, when there was one)."""
    x = o - o.mean()
    if not np.any(x):
        return None, 0.0
    ac = np.correlate(x, x, mode="full")[x.size - 1:]
    ac /= ac[0] + 1e-12
    lo, hi = int(60 * fps / BPM_HI), int(60 * fps / BPM_LO) + 1
    lags = np.arange(max(lo, 2), min(hi, ac.size // 2))
    if lags.size == 0:
        return None, 0.0
    bpm = 60 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpm / BPM_PRIOR) / 0.9) ** 2)
    if hint:
        prior *= 0.6 + 0.4 * np.exp(-0.5 * (np.log2(bpm / hint) / 0.1) ** 2)
    score = (ac[lags] + 0.5 * ac[np.minimum(2 * lags, ac.size - 1)]) * prior
    k = int(lags[np.argmax(score)])
    a, b, c = ac[k - 1], ac[k], ac[k + 1]
    shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
    return 60 * fps / (k + float(np.clip(shift, -0.5, 0.5))), float(np.clip(ac[k] * 3, 0, 1))


def track_beats(o: np.ndarray, period: float, tightness: float = TIGHTNESS) -> np.ndarray:
    """Frame indices of the beats: the path through the onsets that keeps
    closest to the tempo (Ellis 2007)."""
    n = o.size
    if n < 2 * period:
        return np.zeros(0, dtype=int)
    o = o / (o.std() + 1e-9)
    score = o.astype(float).copy()
    back = np.full(n, -1)
    taus = np.arange(max(1, int(round(period / 2))), int(round(period * 2)) + 1)
    pen = -tightness * np.log(taus / period) ** 2
    for t in range(taus[0], n):
        prev = t - taus
        ok = prev >= 0
        cand = score[prev[ok]] + pen[ok]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] += cand[j]
            back[t] = prev[ok][j]
    tail = int(round(period))
    t = n - tail + int(np.argmax(score[n - tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1], dtype=int)


def refine(o: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Sub-frame beat positions (a parabola through each peak)."""
    out = idx.astype(float)
    for k, i in enumerate(idx):
        if 0 < i < o.size - 1:
            a, b, c = o[i - 1], o[i], o[i + 1]
            d = a - 2 * b + c
            if d < 0:
                out[k] = i + float(np.clip(0.5 * (a - c) / d, -0.5, 0.5))
    return out


def at(times: np.ndarray, frame_pos: np.ndarray) -> np.ndarray:
    """Song positions of (fractional) frame indices."""
    return np.interp(times, np.arange(frame_pos.size), frame_pos)


def rows_between(pos: np.ndarray, a: float, b: float) -> slice:
    i, j = np.searchsorted(pos, a), np.searchsorted(pos, b)
    if j <= i:
        j = min(i + 1, pos.size)
    return slice(i, j)


def downbeat_phase(beat_t, fast_pos, fast, pos, chroma, events) -> int:
    """Which of every 4 beats is the bar's first: the one where the kicks land
    hardest and the chords change, and where the song's big moments fall."""
    if beat_t.size < 8:
        return 0
    kick = fast[:, FAST.index("kick")]
    k = np.array([kick[max(0, i - 2):i + 3].max() for i in np.searchsorted(fast_pos, beat_t).clip(0, kick.size - 1)])
    ch = np.array([chroma[rows_between(pos, a, b)].mean(axis=0) if b > a else np.zeros(12)
                   for a, b in zip(beat_t, np.append(beat_t[1:], beat_t[-1] + 0.5))])
    ch = ch / (np.linalg.norm(ch, axis=1, keepdims=True) + 1e-9)
    change = np.zeros(beat_t.size)
    for i in range(2, beat_t.size - 2):
        before, after = ch[i - 2:i].mean(axis=0), ch[i:i + 2].mean(axis=0)
        change[i] = 1 - float(np.dot(before, after) / (np.linalg.norm(before) * np.linalg.norm(after) + 1e-9))
    big = np.zeros(beat_t.size)
    for e in events:
        if e.get("type") in ("hit", "drop") or (e.get("type") == "crash" and e.get("strength", 0) >= 0.5):
            j = int(np.argmin(np.abs(beat_t - e["t"])))
            if abs(beat_t[j] - e["t"]) < 0.15:
                big[j] += 1
    def z(x):
        return (x - x.mean()) / (x.std() + 1e-9)
    s = z(k) + z(change) + 0.5 * z(big)
    return int(np.argmax([s[p::BEATS_PER_BAR].mean() for p in range(BEATS_PER_BAR)]))


# Bars --------------------------------------------------------------------------

def bar_features(bars, beat_t, rec) -> dict:
    """Each bar: its harmony beat by beat, timbre, loudness, drums, cymbals,
    voice and the stereo picture."""
    pos, chroma, mfcc, rows = rec["pos"], rec["chroma"].astype(np.float32), rec["mfcc"], rec["rows"]
    fast_pos, fast = rec["fast_pos"], rec["fast"]
    col = {name: k for k, name in enumerate(ROW)}
    n = len(bars) - 1
    seq = np.zeros((n, BEATS_PER_BAR, 12), dtype=np.float32)
    out = {k: np.zeros(n) for k in ("rms", "bass", "bright", "ring", "centre", "width", "kick", "snare", "hat",
                                    "onset", "voice", "rap", "speech", "music", "level")}
    tim = np.zeros((n, mfcc.shape[1]), dtype=np.float32)
    ears_pos, ears = rec["ears_pos"], rec["ears"]
    ecol = {name: k for k, name in enumerate(EARS)}
    kick_f, snare_f = fast[:, FAST.index("kick")], fast[:, FAST.index("snare")]
    for b in range(n):
        i0, i1 = bars[b], bars[b + 1]
        a, z = beat_t[i0], beat_t[i1]
        for k in range(BEATS_PER_BAR):
            if i0 + k + 1 < beat_t.size:
                r = rows_between(pos, beat_t[i0 + k], beat_t[i0 + k + 1])
                c = chroma[r].mean(axis=0)
                seq[b, k] = c / (np.linalg.norm(c) + 1e-9)
        r = rows_between(pos, a, z)
        tim[b] = mfcc[r].mean(axis=0)
        out["rms"][b] = rows[r, col["rms_db"]].mean()
        out["bass"][b] = rows[r, col["bass_db"]].mean()
        out["bright"][b] = rows[r, col["brightness"]].mean()
        out["ring"][b] = rows[r, col["ring"]].mean()
        out["centre"][b] = rows[r, col["centre"]].mean()
        out["width"][b] = rows[r, col["width"]].mean()
        out["onset"][b] = rows[r, col["onset"]].mean()
        out["level"][b] = rows[r, col["level"]].mean()
        out["hat"][b] = (rows[r, col["hat"]] >= 0.5).mean()
        f = slice(np.searchsorted(fast_pos, a), max(np.searchsorted(fast_pos, z), np.searchsorted(fast_pos, a) + 1))
        beats_in = max(1, i1 - i0)
        out["kick"][b] = min(1.0, (kick_f[f] >= 0.5).sum() / beats_in)
        out["snare"][b] = min(1.0, (snare_f[f] >= 0.5).sum() / beats_in)
        e = rows_between(ears_pos, a - 0.5, z + 0.5) if ears_pos.size else slice(0, 0)
        if ears_pos.size and e.stop > e.start:
            out["voice"][b] = ears[e, ecol["voice"]].mean()
            out["rap"][b] = ears[e, ecol["rap"]].mean()
            out["speech"][b] = ears[e, ecol["speech"]].mean()
            out["music"][b] = ears[e, ecol["music"]].mean()
    out["seq"], out["timbre"] = seq, tim
    return out


def zs(x):
    x = np.asarray(x, dtype=float)
    return (x - x.mean()) / (x.std() + 1e-9)


def energy_of(f) -> np.ndarray:
    """How big each bar is within the song (a z-score: loudness first, then
    the drums, the brightness and the cymbals)."""
    return zs(f["rms"]) + 0.5 * zs(f["kick"] + f["snare"]) + 0.3 * zs(f["bright"]) + 0.3 * zs(f["ring"])


def voice_of(f) -> np.ndarray:
    """How much voice each bar has, within the song (0..1). The ears' singing
    and rapping are small in a full mix but rise and fall with the voice, so
    they're read on a log scale against the song's own; the voice band's
    centre helps (a lead voice sits there)."""
    ears = np.log10(f["voice"] + f["rap"] + 1e-4)
    v = 0.75 * zs(ears) + 0.25 * zs(f["centre"])
    out = 1 / (1 + np.exp(-1.6 * v))
    if (f["voice"] + f["rap"]).max() < 0.004 and f["speech"].max() < 0.2:
        out *= 0.3                                         # no voice anywhere: an instrumental
    return out


def ssm(f) -> dict:
    """Bar-by-bar similarity: harmony (beat by beat, any key), timbre, energy."""
    seq = f["seq"].reshape(len(f["seq"]), -1)              # (bars, 48)
    n = seq.shape[0]
    best = np.full((n, n), -1.0)
    norms = np.linalg.norm(seq, axis=1) + 1e-9
    for shift in range(12):
        rolled = np.roll(f["seq"], shift, axis=2).reshape(n, -1)
        s = (seq @ rolled.T) / (norms[:, None] * (np.linalg.norm(rolled, axis=1)[None, :] + 1e-9))
        best = np.maximum(best, s - (0.0 if shift == 0 else 0.06))
    harm = best
    t = f["timbre"][:, 1:]
    t = (t - t.mean(axis=0)) / (t.std(axis=0) + 1e-9)
    d = np.linalg.norm(t[:, None, :] - t[None, :, :], axis=2)
    sigma = np.median(d[np.triu_indices(n, 1)]) if n > 1 else 1.0
    tim = np.exp(-(d / (sigma + 1e-9)) ** 2)
    e = energy_of(f)
    en = np.exp(-np.abs(e[:, None] - e[None, :]) / 0.8)
    return {"harm": harm, "timbre": tim, "energy": en, "all": 0.5 * harm + 0.3 * tim + 0.2 * en}


def novelty(S: np.ndarray, half: int = NOVELTY_BARS) -> np.ndarray:
    """Foote's checkerboard novelty: how much each bar looks like a change."""
    n = S.shape[0]
    if n < 2:
        return np.zeros(n)
    g = np.arange(-half, half) + 0.5
    taper = np.exp(-(g[:, None] ** 2 + g[None, :] ** 2) / (2 * (half / 2) ** 2))
    K = np.sign(g)[:, None] * np.sign(g)[None, :] * taper
    pad = np.pad(S, half, mode="constant", constant_values=float(S.mean()))
    nov = np.array([np.sum(K * pad[i:i + 2 * half, i:i + 2 * half]) for i in range(n)])
    nov = np.maximum(nov, 0)
    return nov / (nov.max() + 1e-9)


def segment(S: np.ndarray, nov: np.ndarray, forced=()) -> list:
    """Bar indices where sections start (0 first): homogeneous sections, in
    whole phrases, starting where the music changes."""
    n = S.shape[0]
    if n <= MIN_BARS:
        return [0]
    mu = float(np.median(S))
    C = np.zeros((n + 1, n + 1))
    C[1:, 1:] = np.cumsum(np.cumsum(S - mu, axis=0), axis=1)
    def block(a, b):
        return C[b, b] - C[a, b] - C[b, a] + C[a, a]
    forced = set(forced)
    best = np.full(n + 1, -1e18)
    back = np.zeros(n + 1, dtype=int)
    best[0] = 0.0
    for b in range(1, n + 1):
        for a in range(max(0, b - MAX_BARS), b - MIN_BARS + 1 if b < n else b):
            if best[a] <= -1e17 or any(a < f < b for f in forced):
                continue
            ln = b - a
            score = (best[a] + block(a, b) / ln + PHRASE.get(ln, ODD_PHRASE)
                     + NOVELTY_WEIGHT * (nov[a] if a > 0 else 0.0) - SECTION_COST)
            if score > best[b]:
                best[b], back[b] = score, a
        if best[b] <= -1e17 and b == n:
            back[b] = max(0, b - 1)
    starts, b = [], n
    while b > 0:
        a = back[b]
        starts.append(a)
        b = a
    return sorted(set(starts))


def _aligned(M, a, b, c, d) -> float:
    """Two sections' similarity bar by bar, lined up at their starts or their ends."""
    ln = min(b - a, d - c)
    first = np.mean([M[a + m, c + m] for m in range(ln)])
    last = np.mean([M[b - ln + m, d - ln + m] for m in range(ln)])
    return float(max(first, last))


def section_similarity(segs, S, voice) -> tuple:
    """(how alike each pair of sections is against the song's own typical
    pair (a z-score: harmony first, then timbre, less any difference in
    voice), their harmony 0..1)."""
    k = len(segs)
    H, T, V = np.eye(k), np.eye(k), np.zeros((k, k))
    v = [float(voice[a:b].mean()) for a, b in segs]
    for i in range(k):
        for j in range(i + 1, k):
            H[i, j] = H[j, i] = _aligned(S["harm"], *segs[i], *segs[j])
            T[i, j] = T[j, i] = _aligned(S["timbre"], *segs[i], *segs[j])
            V[i, j] = V[j, i] = abs(v[i] - v[j])
    if k < 3:
        return H - 0.5, H
    iu = np.triu_indices(k, 1)
    def z(M):
        vals = M[iu]
        return (M - vals.mean()) / (vals.std() + 1e-6)
    zt = z(T)
    Z = 0.65 * z(H) + 0.35 * zt - 4 * np.maximum(0.0, V - VOICE_APART)
    Z = np.where(zt < TIMBRE_APART, np.minimum(Z, SAME_Z - 0.01), Z)   # sounding clearly different: not the same
    np.fill_diagonal(Z, 10.0)
    return Z, H


def cluster(Z: np.ndarray, H: np.ndarray, same: float = SAME_Z, harm: float = SAME_HARM) -> list:
    """Average-linkage clustering: sections join while they're this alike."""
    groups = [[i] for i in range(Z.shape[0])]
    while len(groups) > 1:
        best, pair = -1e9, None
        for x in range(len(groups)):
            for y in range(x + 1, len(groups)):
                pairs = [(i, j) for i in groups[x] for j in groups[y]]
                zz = np.mean([Z[i, j] for i, j in pairs])
                if zz > best and np.mean([H[i, j] for i, j in pairs]) >= harm:
                    best, pair = zz, (x, y)
        if pair is None or best < same:
            break
        x, y = pair
        groups[x] += groups.pop(y)
    label = [0] * Z.shape[0]
    for g, members in enumerate(sorted(groups, key=min)):
        for i in members:
            label[i] = g
    return label


def roles_for(sections, events) -> None:
    """Names each section's job in place (see ROLES), anchored on the
    chorus: the loudest music that comes back. What leads into a chorus is
    a verse (and, when there are two, the second a pre-chorus); what comes
    between choruses late in the song is a bridge; voiceless sections are
    breaks or solos; the ends are the intro and outro. Drops, builds and
    breakdowns come from the moments the live analysis caught."""
    n = len(sections)
    if not n:
        return
    labels = {}
    for i, s in enumerate(sections):
        labels.setdefault(s["label"], []).append(i)
    energy = np.array([s["energy"] for s in sections])
    median_e = float(np.median(energy))
    repeated = {l: ix for l, ix in labels.items() if len(ix) >= 2}

    def mean(ix, key):
        return float(np.mean([sections[i][key] for i in ix]))
    chorus = None
    if repeated:
        chorus = max(repeated, key=lambda l: mean(repeated[l], "energy") + 0.12 * len(repeated[l])
                     + 0.1 * mean(repeated[l], "voice") - (0.5 if min(repeated[l]) == 0 and mean(repeated[l], "voice") < 0.3 else 0))
    drops = [e["t"] for e in events if e.get("type") == "drop"]
    builds = [e for e in events if e.get("type") == "build" and e.get("state") == "rising"]
    choruses = [i for i, s in enumerate(sections) if s["label"] == chorus]
    role = [None] * n
    for i in choruses:
        role[i] = "chorus"
    beat_s = 0.5
    for i, s in enumerate(sections):
        here = any(abs(t - s["start"]) < 4 * beat_s for t in drops)
        if here and s["energy"] >= median_e and (role[i] == "chorus" or s["voice"] < 0.45):
            role[i] = "drop"
    # the ends
    if role[0] is None and (sections[0]["voice"] < 0.35 or sections[0]["energy"] <= median_e or n == 1):
        role[0] = "intro"
    last_chorus = choruses[-1] if choruses else None
    if n > 1 and role[n - 1] is None:
        role[n - 1] = "outro"
    elif n > 1 and sections[n - 1]["energy"] < 0.2 and (last_chorus is None or last_chorus < n - 1 or n - 1 in choruses):
        role[n - 1] = "outro"                                  # the chorus fading out is the outro
    # runs between the choruses
    anchors = [i for i in range(n) if role[i] in ("chorus", "drop")]
    first = anchors[0] if anchors else n
    seen_before_first = set(sections[i]["label"] for i in range(first))
    i = 0
    while i < n:
        if role[i] is not None:
            i += 1
            continue
        j = i
        while j < n and role[j] is None:
            j += 1
        run = list(range(i, j))                                 # sections i..j-1, then an anchor (or the end)
        leads_in = j < n and role[j] in ("chorus", "drop")
        late = bool(anchors) and i > anchors[0] and len([a for a in anchors if a < i]) >= 2
        for k in run:
            s = sections[k]
            rising = any(s["start"] - 1 <= b["t"] < s["end"] for b in builds)
            if s["voice"] < 0.3 and 0 < k < n - 1:
                role[k] = "solo" if s["energy"] >= median_e else "breakdown" if s["energy"] < 0.35 else "interlude"
            elif leads_in and k == run[-1] and len(run) >= 2 and (rising or s["bars"] <= 8):
                role[k] = "build" if rising and s["voice"] < 0.4 else "pre-chorus"
            elif late and s["label"] not in seen_before_first and len(labels[s["label"]]) == 1:
                role[k] = "bridge"
            elif s["energy"] < 0.3 and 0 < k < n - 1 and k - 1 >= 0 and sections[k - 1]["energy"] > s["energy"] + 0.25:
                role[k] = "breakdown"
            else:
                role[k] = "verse"
        i = j
    for s, r in zip(sections, role):
        s["role"] = r
    big = [i for i, r in enumerate(role) if r in ("chorus", "drop")]
    if big:
        sections[big[-1]]["last"] = True                       # the last chorus: the song's send-off
    sections[int(np.argmax(energy))]["peak"] = True


def study(rec: dict) -> dict:
    """A recording (memory.Recording.arrays, or load_arrays) -> the song's map."""
    fps = rec["fps"]
    events = rec["events"]
    o = onset_envelope(rec["fast"])
    hint = None
    tempi = [e.get("tempo") for e in events if e.get("type") == "tempo"]
    if tempi:
        hint = float(np.median(tempi))
    bpm, beat_sure = tempo_of(o, fps, hint)
    pos = rec["pos"]
    duration = rec["identity"].get("duration") or (float(pos[-1]) if pos.size else 0.0)
    sections, beat_t, downbeats = [], np.zeros(0), np.zeros(0)
    if bpm:
        idx = track_beats(o, 60 * fps / bpm)
        beat_t = at(refine(o, idx), rec["fast_pos"])
        phase = downbeat_phase(beat_t, rec["fast_pos"], rec["fast"], pos, rec["chroma"].astype(np.float32), events)
        bars = list(range(phase, beat_t.size - 1, BEATS_PER_BAR))
        if bars and bars[0] > 0:
            pass
        if len(bars) >= 4:
            downbeats = beat_t[bars]
            f = bar_features(bars + ([bars[-1] + BEATS_PER_BAR] if bars[-1] + BEATS_PER_BAR < beat_t.size else []),
                             beat_t, rec)
            nb = len(f["rms"])
            S = ssm(f)
            nov = np.maximum(novelty(S["all"], NOVELTY_BARS), 0.6 * novelty(S["all"], 2))
            forced = []
            for e in events:
                if e.get("type") == "drop":
                    j = int(np.argmin(np.abs(downbeats[:nb] - e["t"])))
                    if abs(downbeats[j] - e["t"]) < 60 / bpm * 2 and 0 < j < nb:
                        forced.append(j)
            starts = segment(S["all"], nov, forced)
            ends = starts[1:] + [nb]
            segs = list(zip(starts, ends))
            en = energy_of(f)
            voice = voice_of(f)
            labels = cluster(*section_similarity(segs, S, voice))
            seg_e = np.array([en[a:b].mean() for a, b in segs])
            lo, hi = seg_e.min(), seg_e.max()
            rank = (seg_e - lo) / (hi - lo + 1e-9)
            letters = {}
            count = {}
            bar_t = list(downbeats[:nb]) + [float(beat_t[min(bars[-1] + BEATS_PER_BAR, beat_t.size - 1)])
                                           if nb == len(bars) else float(downbeats[nb] if nb < len(downbeats) else beat_t[-1])]
            for k, ((a, b), lab) in enumerate(zip(segs, labels)):
                letter = letters.setdefault(lab, chr(ord("A") + len(letters)))
                count[letter] = count.get(letter, 0) + 1
                drums = float(np.clip(0.6 * f["kick"][a:b].mean() + 0.4 * f["snare"][a:b].mean(), 0, 1))
                sections.append({
                    "start": round(float(bar_t[a]), 3), "end": round(float(bar_t[b]), 3), "bars": int(b - a),
                    "label": letter, "n": count[letter], "energy": round(float(rank[k]), 3),
                    "voice": round(float(voice[a:b].mean()), 3), "drums": round(drums, 3),
                    "ring": round(float(np.clip(f["ring"][a:b].mean(), 0, 1)), 3), "tempo": bpm,
                })
            roles_for(sections, events)
            for s in sections:
                s.pop("tempo", None)
            if sections:
                sections[0]["start"] = 0.0
                if duration and sections[-1]["end"] < duration:
                    sections[-1]["end"] = round(float(duration), 3)
    if not sections and duration:
        sections = [{"start": 0.0, "end": round(float(duration), 3), "bars": 0, "label": "A", "n": 1,
                     "energy": 0.5, "voice": 0.5, "drums": 0.5, "ring": 0.0, "role": "verse", "peak": True}]
    key = Key()
    w = rec["rows"][:, ROW.index("sounding")]
    for c, s in zip(rec["chroma"].astype(np.float32), w):
        key.add(c, s)
    ears = rec["ears"]
    def ear(name):
        return round(float(ears[:, EARS.index(name)].mean()), 4) if ears.size else 0.0
    genres = {}
    for g in rec["genres"]:
        for name, v in g.items():
            genres[name] = genres.get(name, 0.0) + v / max(1, len(rec["genres"]))
    sounding = w > 0.5
    bright = float(rec["rows"][sounding, ROW.index("brightness")].mean()) if sounding.any() else None
    span = sum(s["end"] - s["start"] for s in sections) or 1.0
    drums = sum(s["drums"] * (s["end"] - s["start"]) for s in sections) / span
    voiced = sum(s["end"] - s["start"] for s in sections if s["voice"] >= VOICED) / span
    ident = rec["identity"]
    the_feel = feel_of(ident.get("tags") or (), ident.get("genre"), ident.get("year"),
                       {"tempo": bpm, "drums": drums, "brightness": bright},
                       {"moods": {k: ear(k) for k in ("happy", "sad", "tender", "exciting", "angry", "scary")}},
                       key.read(), voiced)
    moments = []
    for e in events:
        if e.get("type") in ("gap", "hit", "drop", "crash") or (e.get("type") == "build" and e.get("state") in ("rising", "breakdown")):
            moments.append({k: v for k, v in e.items() if k != "v"})
    return {
        "format": FORMAT,
        "song": {k: rec["identity"].get(k) for k in ("title", "artist", "album")} | {"duration": duration},
        "learned": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "plays": 1,
        "tempo": round(bpm, 2) if bpm else None,
        "beats": [round(float(t), 3) for t in beat_t],
        "downbeats": [round(float(t), 3) for t in downbeats],
        "sections": sections,
        "moments": moments,
        "key": key.read(),
        "ears": {k: ear(k) for k in EARS},
        "genres": {k: round(v, 4) for k, v in sorted(genres.items(), key=lambda kv: -kv[1])[:6]},
        "feel": the_feel,
        "confidence": {"beats": round(beat_sure, 3)},
    }


def main():
    """python -m services.jb_analyzer.study rec.npz [map.json]: study a
    recording (jb-analyzer runs this niced, after a song)."""
    rec = load_arrays(sys.argv[1])
    m = study(rec)
    out = json.dumps(m, separators=(",", ":")) + "\n"
    if len(sys.argv) > 2:
        write_atomic(Path(sys.argv[2]), out)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
