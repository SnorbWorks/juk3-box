"""Replays: where in a learned song the cabinet is now, so the lights can
follow its map (and see what's coming).

A map's times are song positions as its learning play heard them: the
source's position then, plus whatever delay its audio had (a phone over
Bluetooth, AirPlay and a USB drive all differ). Playing again, the source
says roughly where it is; the sound says exactly. The last WINDOW_S of the
live harmony and onsets are matched against the recording's, SEARCH_S
either side of where the source puts it (or the whole song, when it says
nothing): the harmony finds the bar, the onsets the frame. Locked, the
position follows the clock, checked every EVERY_S; a source jumping (a seek,
a pause) or the match drifting away unlocks it, and it finds its place again.

  Sync(rec)                 the recording (memory.load_arrays): fast_pos/fast,
                            pos/chroma
  Sync.frame(t, f)          every fast frame (the fast layer's output)
  Sync.texture(t, tex)      every texture frame
  Sync.source(t, pos_s)     the source's position, when it says
  Sync.assume(t, pos_s)     where it probably is, with no word from the source
                            (a song heard starting: its start)
  Sync.position(t)          -> the song position at t (s), or None
  Sync.sure                 0..1, how sure it is
Pure numpy.
"""

from collections import deque

import numpy as np

from services.jb_analyzer.memory import FAST

WINDOW_S = 8.0               # the live sound matched at once
SEARCH_S = 3.0               # either side of where the source says
TRACK_S = 0.35               # once locked: either side of where we think
EVERY_S = 2.0
MIN_MATCH = 0.3              # a match at least this good (correlation) counts
LOCK_AFTER = 2               # agreeing matches in a row to lock
AGREE_S = 0.12               # two matches agree within this
JUMP_S = 1.5                 # the source this far off the clock: it jumped
PRIOR = 0.15                 # how much a match far from where the source says loses (per SEARCH_S, squared)
LOST_AFTER = 4               # failed checks in a row: unlocked
UNIQUE_S = 1.5               # searching the whole song: the best place must beat any other this far away...
UNIQUE_BY = 0.1              # ...by this much
ASSUMED = 0.05               # how much being near an assumed place (assume()) counts


def _z(x):
    x = np.asarray(x, dtype=np.float64)
    return (x - x.mean()) / (x.std() + 1e-9)


def onsets(flux, kick) -> np.ndarray:
    """The study's onset envelope (study.onset_envelope), for a stretch."""
    o = _z(np.log1p(np.maximum(flux, 0))) + 0.5 * _z(kick)
    k = 15
    o = o - np.convolve(o, np.ones(k) / k, mode="same")
    return np.maximum(o, 0)


class Sync:
    def __init__(self, rec: dict):
        fps = float(rec["fps"])
        self.fps = fps
        fast = np.asarray(rec["fast"], dtype=np.float64)
        self.ref_t = np.asarray(rec["fast_pos"], dtype=np.float64)
        self.ref = _z(onsets(fast[:, FAST.index("flux")], fast[:, FAST.index("kick")]))
        self.ref_ct = np.asarray(rec["pos"], dtype=np.float64)
        c = np.asarray(rec["chroma"], dtype=np.float64)
        self.ref_c = c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-9)
        self.tex_fps = 1.0 / float(np.median(np.diff(self.ref_ct))) if self.ref_ct.size > 2 else fps / 3
        n = int(WINDOW_S * fps * 1.2)
        self.live_t, self.live = deque(maxlen=n), deque(maxlen=n)
        self.live_ct, self.live_c = deque(maxlen=int(WINDOW_S * self.tex_fps * 1.2)), deque(
            maxlen=int(WINDOW_S * self.tex_fps * 1.2))
        self.anchor = None           # (t, pos) the source said last
        self.assumed = None          # (t, pos) where it probably is, with no anchor
        self.offset = None           # song position - clock, once locked
        self.trial = []              # recent matches' offsets, before a lock
        self.sure = 0.0
        self.misses = 0
        self.next_check = None

    # Input ----------------------------------------------------------------------

    def frame(self, t: float, f: dict):
        self.live_t.append(t)
        self.live.append((f.get("_flux", 0.0), f.get("kick", 0.0)))
        if self.next_check is None:
            self.next_check = t + WINDOW_S
        if t >= self.next_check:
            self.next_check = t + EVERY_S
            self._check(t)

    def texture(self, t: float, tex: dict):
        self.live_ct.append(t)
        self.live_c.append(np.asarray(tex["chroma"], dtype=np.float64))

    def source(self, t: float, pos_s: float | None):
        if pos_s is None:
            return
        pos_s = float(pos_s)
        prev, self.anchor = self.anchor, (t, pos_s)
        jumped = prev is not None and abs((pos_s - prev[1]) - (t - prev[0])) > JUMP_S
        if jumped and self.offset is not None:
            self.offset, self.trial, self.sure = None, [], 0.0          # a seek: find our place again
            self.next_check = t + WINDOW_S

    def assume(self, t: float, pos_s: float):
        if self.anchor is None:
            self.assumed = (t, float(pos_s))

    # Where we are --------------------------------------------------------------------

    def position(self, t: float) -> float | None:
        if self.offset is not None:
            return t + self.offset
        if self.anchor is not None:
            # not locked yet: the source's word (its delay unknown)
            return self.anchor[1] + (t - self.anchor[0])
        return None

    def _guess(self, t):
        """(where it probably is, how far either side to look -- None: the whole
        song, and only a place that stands out), or (None, None)."""
        if self.offset is not None:
            return t + self.offset, TRACK_S
        if self.anchor is not None:
            return self.anchor[1] + (t - self.anchor[0]), SEARCH_S
        if self.assumed is not None:
            return self.assumed[1] + (t - self.assumed[0]), None
        return None, None

    def _check(self, t):
        n = int(WINDOW_S * self.fps)
        if len(self.live) < n:
            return
        end_t = self.live_t[-1]
        guess, span = self._guess(end_t)
        found = self._match(end_t, guess, span)
        if found is None:
            self.misses += 1
            if self.offset is not None and self.misses >= LOST_AFTER:
                self.offset, self.trial, self.sure, self.misses = None, [], 0.0, 0
            return
        pos, score = found
        off = pos - end_t
        if self.offset is not None or self.anchor is not None:
            self.misses = 0
        if self.offset is not None:
            if abs(off - self.offset) <= TRACK_S:
                self.offset += 0.5 * (off - self.offset)                # follow gently
                self.sure = min(1.0, 0.7 * self.sure + 0.3 * min(1.0, score / 0.6))
            return
        self.trial = [o for o in self.trial if abs(o - off) <= AGREE_S] + [off]
        if len(self.trial) >= LOCK_AFTER:
            self.offset = float(np.median(self.trial))
            self.sure = min(1.0, score / 0.6)
            self.trial = []

    def _match(self, end_t, guess, span):
        """(the song position matching the live sound's end, how well), or None."""
        live = np.array(self.live)[-int(WINDOW_S * self.fps):]
        lt = np.array(self.live_t)[-live.shape[0]:]
        o = _z(onsets(live[:, 0], live[:, 1]))
        song_end = float(self.ref_t[-1])
        whole = span is None
        lo, hi = (0.0, song_end) if whole else (max(0.0, guess - span), min(song_end, guess + span))
        if hi - lo < 0.05:
            return None
        # the harmony first (10 a second), to find the bar
        cands = np.arange(lo, hi + 1e-9, 1.0 / self.tex_fps)
        if cands.size == 0:
            return None
        score = np.zeros(cands.size)
        if len(self.live_c) >= int(WINDOW_S * self.tex_fps * 0.8):
            lc = np.array(self.live_c)
            lc = lc / (np.linalg.norm(lc, axis=1, keepdims=True) + 1e-9)
            back = end_t - np.array(self.live_ct)                         # how long before the end
            keep = back <= WINDOW_S
            lc, back = lc[keep], back[keep]
            idx = np.searchsorted(self.ref_ct, cands[:, None] - back[None, :]).clip(0, self.ref_ct.size - 1)
            score = np.einsum("kjc,jc->k", self.ref_c[idx], lc) / max(1, lc.shape[0])
            score = 0.5 * (score - np.median(score))                     # only how it stands out counts
        # then the onsets around each, frame by frame
        rel = lt - lt[-1]
        shift = np.arange(-1, 2) / self.fps
        q = (cands[:, None] + shift[None, :]).reshape(-1)                # every candidate, a frame either side
        idx = np.searchsorted(self.ref_t, q[:, None] + rel[None, :]).clip(0, self.ref_t.size - 1)
        c = (self.ref[idx] @ o / o.size).reshape(cands.size, shift.size)
        s = c + score[:, None]
        qs = q.reshape(cands.size, shift.size)
        if whole:
            if guess is not None:                        # an assumption only breaks ties
                s = s + ASSUMED * np.exp(-((qs - guess) / SEARCH_S) ** 2)
        elif span > TRACK_S:
            s = s - PRIOR * ((qs - guess) / span) ** 2
        j = np.argmax(s, axis=1)
        rows = np.arange(cands.size)
        per, at, corr = s[rows, j], qs[rows, j], c[rows, j]
        k = int(np.argmax(per))
        if corr[k] < MIN_MATCH:
            return None
        if whole:
            # no word from the source: only a place clearly better than anywhere else in the song
            far = np.abs(at - at[k]) > UNIQUE_S
            if far.any() and per[k] - per[far].max() < UNIQUE_BY:
                return None
        return float(at[k]), float(corr[k])
