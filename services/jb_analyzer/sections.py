"""Live sections: a song's parts as they happen, the first time it plays.

Ten times a second (the texture frames), what the music held: its harmony
(chroma), timbre, loudness, kicks, snares, the cymbals' ring and the voice.

At every frame, the bar's worth of music starting there (a bar: four beats
at the song's tempo) is compared with the bars' worth starting 1, 2, 4 and
8 bars back within the section it's in: its harmony beat by beat and its
timbre against the closest of them (a loop going round always matches one;
a new section -- or one from before coming back -- matches none),
its energy and voice against the four bars before. Where a section starts,
that change peaks. Bars are measured in time from the tempo, not by counting
the beat clock's beats: its count slips a beat now and then, and one slip
would put every bar after it out of step.

A section starts on a peak that stands out from the song's own changes so
far -- more readily a whole phrase (4, 8 or 16 bars) after the last one,
less readily off the bar lines that one set; at once on a drop or a big hit.
Its first bar is matched with the sections heard so far: the same music
coming back gets the same label (the chorus again), checked once more a bar
later. A voiced and a voiceless section are hardly ever the same music (a
verse and its instrumental break share chords).

Roles are guesses that the study names properly after the song: the first
section is the intro (or a verse, with a voice); a section louder than any
before is a chorus; the label that comes back loudest is the chorus, the
others that come back verses; voiceless ones are breaks or solos; a drop
starts a drop.

  LiveSections.texture(tex, rms_db, t, tempo)   every texture frame -> section
     events: {"type": "section", "label", "role", "n", "size" (0..1, how big
     within the song so far), "voice", "drums", "change", "energy"
     ("up"/"down"/"same"), "start", "source": "live"}, and the same with
     "update": true when a bar later says otherwise. A section is known about
     a bar and a beat after it starts.
Pure numpy, no I/O.
"""

import numpy as np

BEATS = 4                    # a bar
LAGS = (1, 2, 4, 8)          # bars back a loop might repeat from
CONTEXT_BARS = 4
MIN_BARS = 4                 # a section lasts at least this long (unless a drop or a hit cuts it)
CHANGE = 1.5                 # a peak this many times the song's typical change can start a section...
OUTLIER = 2.0                # ...if it's this many spreads above the song's changes so far...
PHRASE_EASE = {8: 0.8, 4: 1.0, "bar": 1.3}   # ...more readily 8 (16, 24..) or 4 bars after the last section
OFF_GRID = 1.6               # ...much less readily off the bar lines the last one set
GRID_BEATS = 1.0             # a change this close to a bar line is on it
PEAK_BEATS = 1.0             # a peak is the highest change within this many beats either side
LATE_PEAK_BEATS = 1.5        # a bar line's change may peak this long after it (later: it's the next line's)
WARM_S = 6.0                 # the song's typical change settles over this long
SAME = 0.86                  # first bars this alike (harmony, 0..1): the same music coming back
VOICE_APART = 0.3            # voice further apart than this counts against the same music
CHROMA_LAG_S = 0.17          # a texture frame's harmony is centred this far before it (a 0.34 s FFT)
# The song's typical change in harmony, timbre and energy is at least this: a loop going round exactly
# (a lot of dance music) must not make every little wobble a new section. Real songs sit well above.
TYPICAL_FLOOR = np.array([0.02, 0.25, 0.5])


def _cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class Frame:
    __slots__ = ("t", "chroma", "timbre", "rms", "kick", "snare", "ring", "voice")


class Window:
    """The bar's worth of music from one frame."""
    __slots__ = ("seq", "timbre", "energy", "voice", "drums", "score")


class LiveSections:
    def __init__(self):
        self.reset()

    def reset(self):
        self.frames, self.windows, self.sections = [], [], []
        self.drums = {"kick": 0.0, "snare": 0.0, "ring": 0.0}    # since the last texture frame
        self.voice_now = 0.0
        self.voice_hist = []
        self.forced = None           # (when a drop or a big hit landed, which)
        self.pending = None          # a section to check again a bar later
        self.parts = []              # each window's harmony, timbre and energy change
        self.checked = -1            # the last frame looked at for a peak
        self.bar_s = None

    # Input ------------------------------------------------------------------------

    def frame(self, f: dict, t: float | None = None):
        """Every fast frame: the drums and the cymbals."""
        d = self.drums
        for k in d:
            d[k] = max(d[k], f.get(k, 0.0))

    def heard(self, groups: dict):
        """The ears' latest groups: the voice, on a log scale (it's small in a full mix)."""
        v = float(np.log10(groups.get("voice", 0.0) + groups.get("rap", 0.0) + 1e-4))
        self.voice_hist.append(v)
        lo, hi = (min(self.voice_hist), max(self.voice_hist)) if len(self.voice_hist) > 4 else (-4.0, -1.0)
        self.voice_now = float(np.clip((v - lo) / (hi - lo + 1e-6), 0, 1)) if hi - lo > 0.3 else 0.0

    def moment(self, e: dict, t: float):
        if e.get("type") == "drop" or (e.get("type") == "hit" and e.get("strength", 0) >= 0.6):
            self.forced = (t, e["type"])

    def texture(self, tex: dict, rms_db: float, t: float, tempo: float | None) -> list:
        """Every texture frame, with the song's tempo (or None, not known yet).
        Returns any section events."""
        fr = Frame()
        fr.t, fr.voice = t - CHROMA_LAG_S, self.voice_now
        fr.chroma, fr.timbre, fr.rms = np.asarray(tex["chroma"], dtype=float), np.asarray(tex["mfcc"]), rms_db
        d = self.drums
        fr.kick, fr.snare, fr.ring = d["kick"], d["snare"], d["ring"]
        self.drums = {k: 0.0 for k in d}
        self.frames.append(fr)
        if not tempo:
            return []
        self.bar_s = BEATS * 60.0 / tempo
        out = []
        # every frame whose bar is now heard through
        while len(self.windows) < len(self.frames) and self.frames[len(self.windows)].t + self.bar_s <= fr.t:
            out += self._window_done(len(self.windows))
        return out

    # The music just heard ---------------------------------------------------------

    def _at(self, t) -> int:
        """The frame nearest time t."""
        lo, hi = 0, len(self.frames) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.frames[mid].t < t:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0 and abs(self.frames[lo - 1].t - t) <= abs(self.frames[lo].t - t):
            lo -= 1
        return lo

    def _window_done(self, j) -> list:
        self._window(j)
        out = []
        s = self.pending
        if s is not None and self.frames[j].t >= s["start"] + self.bar_s:
            out += self._recheck()
        if not self.sections:
            out.append(self._start(j, "intro" if self.windows[j].voice < 0.3 else "verse"))
            return out
        beat_s = self.bar_s / BEATS
        if self.forced is not None and self.forced[0] - CHROMA_LAG_S <= self.frames[j].t:
            t, kind = self.forced
            self.forced = None
            if t - CHROMA_LAG_S - self.sections[-1]["start"] >= self.bar_s:
                out.append(self._start(self._at(t - CHROMA_LAG_S), None, dropped=kind == "drop", change=1.0))
                self.checked = j
            return out
        now = self.frames[j].t
        tol = GRID_BEATS * beat_s
        # the bar lines after the last section, each once the music a beat past it is in
        half = self.bar_s / 2
        while len(self.sections) > 1 or self.sections[0]["frame"] > 0:
            last = self.sections[-1]
            k = max(MIN_BARS, last.get("next_bar", MIN_BARS))
            g = last["start"] + k * self.bar_s
            if now < g + half:
                break
            last["next_bar"] = k + 1
            near = [i for i in range(self._at(g - tol), j + 1)
                    if -tol <= self.frames[i].t - g <= half and self.windows[i].score is not None]
            line = [i for i in near if abs(self.frames[i].t - g) <= tol]
            if not line:
                continue
            p = max(line, key=lambda i: self.windows[i].score)
            # a change still rising half a bar on is the next bar line's, not this one's
            top = max(near, key=lambda i: self.windows[i].score)
            if self.frames[top].t - g > LATE_PEAK_BEATS * beat_s:
                continue
            if self._is_change(p, self._ease(k)):
                # between the bar line and the peak: a slightly early or late first section corrects itself
                at = self._at(g + 0.5 * (self.frames[p].t - g))
                out.append(self._start(at, None, change=self.windows[p].score / 3))
                self.checked = j
            break
        # a peak off the bar lines (or with no bar lines yet): now that the frames a beat after it are in
        while True:
            p = self.checked + 1
            if p >= j or now - self.frames[p].t < PEAK_BEATS * beat_s:
                break
            self.checked = p
            last = self.sections[-1]
            sc = self.windows[p].score
            since = self.frames[p].t - last["start"]
            if sc is None or since < MIN_BARS * self.bar_s - tol:
                continue
            gridded = len(self.sections) > 1 or last["frame"] > 0
            if gridded and abs(since / self.bar_s - round(since / self.bar_s)) * self.bar_s <= tol:
                continue                                  # a bar line's: judged there
            near = [self.windows[i].score for i in range(self._at(self.frames[p].t - PEAK_BEATS * beat_s), j + 1)
                    if i != p and self.windows[i].score is not None
                    and abs(self.frames[i].t - self.frames[p].t) <= PEAK_BEATS * beat_s]
            if near and sc <= max(near):
                continue
            if self._is_change(p, OFF_GRID if gridded else 1.0):
                out.append(self._start(p, None, change=sc / 3))
        return out

    @staticmethod
    def _ease(bars) -> float:
        """How readily a section starts this many bars after the last one."""
        if bars % 8 == 0:
            return PHRASE_EASE[8]
        if bars % 4 == 0:
            return PHRASE_EASE[4]
        return PHRASE_EASE["bar"]

    def _window(self, j):
        """The bar's worth from frame j: its harmony beat by beat, timbre,
        energy, voice and drums; and how much it breaks from before."""
        t0, bar_s = self.frames[j].t, self.bar_s
        k = j
        beats = [[] for _ in range(BEATS)]
        while k < len(self.frames) and self.frames[k].t < t0 + bar_s:
            beats[min(BEATS - 1, int((self.frames[k].t - t0) / bar_s * BEATS))].append(self.frames[k])
            k += 1
        allf = [f for b in beats for f in b]
        beats = [b or beats_prev for b, beats_prev in zip(beats, [allf] * BEATS)]
        seq = []
        for b in beats:
            c = np.sum([f.chroma for f in b], axis=0)
            seq.append(c / (np.linalg.norm(c) + 1e-9))
        kick = float(np.mean([max(f.kick for f in b) >= 0.5 for b in beats]))
        snare = float(np.mean([max(f.snare for f in b) >= 0.5 for b in beats]))
        w = Window()
        w.seq = np.concatenate(seq)
        w.timbre = np.mean([f.timbre for f in allf], axis=0)
        w.energy = float(np.mean([f.rms for f in allf])) + 6 * (0.6 * kick + 0.4 * snare) \
            + 4 * max(f.ring for f in allf)
        w.voice = float(np.mean([f.voice for f in allf]))
        w.drums = 0.6 * kick + 0.4 * snare
        w.score = None
        self.windows.append(w)
        # a loop going round repeats something from its own section: music from before it coming
        # back (the chorus after a short break) is a change all the same
        since = self.sections[-1]["start"] if self.sections else self.frames[0].t
        slack = 0.5 * bar_s / BEATS
        lags = [i for i in (self._at(t0 - n * bar_s) for n in LAGS if t0 - n * bar_s >= since - slack) if i < j]
        ctx = [i for i in (self._at(t0 - n * bar_s) for n in range(1, CONTEXT_BARS + 1)
                           if t0 - n * bar_s >= self.frames[0].t - slack) if i < j]
        if not lags or not ctx:
            return
        h = min(1 - _cos(w.seq, self.windows[i].seq) for i in lags)
        tim = min(float(np.linalg.norm(w.timbre[1:] - self.windows[i].timbre[1:])) for i in lags)
        e = abs(w.energy - float(np.mean([self.windows[i].energy for i in ctx])))
        v = abs(w.voice - float(np.mean([self.windows[i].voice for i in ctx])))
        self.parts.append((h, tim, e))
        if len(self.parts) < 10:
            w.score = 0.0
            return
        typical = np.maximum(np.median(np.array(self.parts[:-1]), axis=0), TYPICAL_FLOOR)
        w.score = float(0.45 * h / typical[0] + 0.25 * tim / typical[1] + 0.3 * e / typical[2]
                        + 2.0 * max(0.0, v - 0.3))

    def _is_change(self, p, ease) -> bool:
        """A change that stands out from the song's own change scores so far
        (eased: more readily a whole phrase after the last section)."""
        hist = np.array([w.score for w in self.windows[:p] if w.score is not None])
        if hist.size == 0 or self.frames[p].t - self.frames[0].t < WARM_S:
            return False
        med = float(np.median(hist))
        spread = 1.4826 * float(np.median(np.abs(hist - med))) + 1e-6
        return self.windows[p].score >= max(CHANGE, med + OUTLIER * spread) * ease

    def _match(self, j, upto=1, before=None) -> tuple:
        """(the label of the earlier section whose first bars are most like the
        bars from frame j (upto of them), how alike), or (None, 0)."""
        best, sim = None, 0.0
        for s in self.sections if before is None else self.sections[:before]:
            k = s["frame"]
            ms = []
            for u in range(upto):
                a, b = self._at(self.frames[j].t + u * self.bar_s), self._at(self.frames[k].t + u * self.bar_s)
                if a >= len(self.windows) or b >= j:
                    break
                wa, wb = self.windows[a].seq, self.windows[b].seq
                ms.append(np.mean([_cos(wa[12 * q:12 * q + 12], wb[12 * q:12 * q + 12]) for q in range(BEATS)]))
            if len(ms) < upto:
                continue
            m = float(np.mean(ms)) - 0.5 * max(0.0, abs(self.windows[j].voice - self.windows[k].voice) - VOICE_APART)
            if m > sim:
                best, sim = s["label"], m
        return best, sim

    def _size(self, j) -> float:
        """How big the bar from frame j is against the song so far (0..1)."""
        e = np.array([w.energy for w in self.windows])
        lo, hi = float(np.percentile(e, 10)), float(np.percentile(e, 95))
        return float(np.clip((e[j] - lo) / (hi - lo + 1e-6), 0, 1)) if hi - lo > 1 else 0.5

    def _start(self, j, role, dropped=False, change=1.0) -> dict:
        w = self.windows[min(j, len(self.windows) - 1)]
        label, sim = self._match(j) if self.sections else (None, 0.0)
        if label is None or sim < SAME:
            label = chr(ord("A") + len({s["label"] for s in self.sections}))
        size = self._size(min(j, len(self.windows) - 1))
        n = 1 + sum(1 for s in self.sections if s["label"] == label)
        if role is None:
            role = self._role(label, size, w.voice, dropped)
        prev = self.sections[-1]["size"] if self.sections else size
        s = {"start": round(self.frames[j].t, 3), "frame": j, "label": label, "role": role, "n": n,
             "size": round(size, 3), "voice": round(w.voice, 3), "drums": round(w.drums, 3)}
        self.sections.append(s)
        self.pending = s if j > 0 else None
        # change and energy (up, down, same): the fields section events have always carried
        return {"type": "section", **self._public(s), "change": round(float(min(1.0, change)), 2),
                "energy": "up" if size > prev + 0.15 else "down" if size < prev - 0.15 else "same"}

    def _role(self, label, size, voice, dropped) -> str:
        if dropped:
            return "drop"
        before = [s for s in self.sections if s["label"] == label]
        if before:
            role = before[-1]["role"]
            return "verse" if role in ("intro", "outro") else role
        if voice < 0.25:
            return "solo" if size >= 0.6 else "interlude"
        voiced = [s for s in self.sections if s["voice"] >= 0.25]
        if len(voiced) >= 2 and size >= 0.85 and size >= max(s["size"] for s in voiced):
            return "chorus"                              # (after an intro comes a verse, however big)
        return "verse"

    def _recheck(self) -> list:
        """A bar later: does the new section still look like the label it got?"""
        s, self.pending = self.pending, None
        j = s["frame"]
        later = self._at(self.frames[j].t + self.bar_s)
        s["size"] = round((self._size(j) + self._size(min(later, len(self.windows) - 1))) / 2, 3)
        label, sim = self._match(j, upto=2, before=len(self.sections) - 1)
        changed = False
        if label is not None and sim >= SAME and label != s["label"]:
            s["label"] = label
            s["n"] = 1 + sum(1 for x in self.sections[:-1] if x["label"] == label)
            s["role"] = self._role(label, s["size"], s["voice"], s["role"] == "drop")
            changed = True
        # the label coming back loudest is the chorus
        reps = {}
        for x in self.sections:
            reps.setdefault(x["label"], []).append(x)
        repeated = {l: xs for l, xs in reps.items() if len(xs) >= 2}
        if s["label"] in repeated and s["role"] != "drop":
            # how big each one's first two bars are now -- against the whole song so far, not as it seemed then
            def big(x):
                k = x["frame"]
                return np.mean([self.windows[i].energy for i in (k, self._at(self.frames[k].t + self.bar_s))
                                if i < len(self.windows)])
            loud = max(repeated, key=lambda l: np.mean([big(x) for x in repeated[l]]))
            typical = np.median([big(x) for x in self.sections])
            if np.mean([big(x) for x in repeated[loud]]) < typical:
                loud = None                              # nothing loud has come back yet
            role = "chorus" if s["label"] == loud else ("verse" if s["role"] in ("intro", "chorus") else s["role"])
            if role != s["role"]:
                s["role"], changed = role, True
        return [{"type": "section", "update": True, **self._public(s)}] if changed else []

    @staticmethod
    def _public(s) -> dict:
        return {**{k: v for k, v in s.items() if k not in ("frame", "next_bar")}, "source": "live"}

    def summary(self) -> list:
        """The sections so far, for the live map."""
        return [self._public(s) for s in self.sections]
