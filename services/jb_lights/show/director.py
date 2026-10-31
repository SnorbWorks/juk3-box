"""The show's director: jb-analyzer's stream in, cues for every part out.

What it decides, moment to moment (docs/Light Show Styles.md):
  plan     per song (show/plan.py): the show that fits it, one colour
           family, one pace and smoothness, its accents -- made as the song
           starts, again as its feel, tempo, genre and tags come in, then
           held (SETTLE_S; a song the cabinet has learned has its feel at once)
  look     per section: the plan's look for it (by label, so the chorus
           again looks like the chorus) from the analyzer's section events,
           or -- a learned song locked to its map -- from the map itself, a
           moment early so it lands on the beat. A breakdown thins it to the
           break look until the music comes back; a drop with no section
           yet gets the peak look; before the first section, a look for
           how the music sounds (quiet, vocal, drive), held a few bars
  palette  the look's colours, crossfading at the plan's smoothness; a cut
           (a drop slamming in) changes at once
  beat     where we are in the beat and the bar (the analyzer's beat clock,
           carried forward between its frames), and the movement: steps at
           the plan's rate (a ballad moves once a bar), quicker in a build
  drums    a pulse 0..1 for the kick, the snare, the off-beat and the hats,
           each fading at the plan's release; the crash cymbal rings on and
           splashes across the cabinet (a ballad's crash only shimmers)
  black    a gap blacks the cabinet out (or dims it) until the hit
  lift     the drop, or the hit after a gap, slams the brightness up and
           lets it settle over a beat or two (a ballad swells instead)
  flash    a big hit or a drop flashes white, in shows and songs that flash
           -- never more than MAX_FLASHES_PER_S (photosensitive safety:
           WCAG 2.3.1, Ofcom)
  burst    for styles that draw their own flash (arena): the drop itself,
           or a really big hit. Not a section lifting or the hit after a
           gap: those come just before the drop, so the flash would lead
           the beat (owner, 2026-10-30). At most one every BURST_MIN_S,
           but a drop may come sooner (never closer than DROP_MIN_S)
  tension  a build rising -- or, with a map, the bars before a big section --
           the upper panel holds back and the rhythm tightens; cool: a
           breakdown thins everything out
With a map the director also sees the song's moments coming: a gap goes
dark as the sound falls away (the analyzer calls it ~0.1 s in), and a live
moment the map already played isn't played twice.

Every part's thread asks for cues (the feed's snapshot), so update() runs a
few times a frame: everything passing is timed, never a one-shot flag.
Pure: update() reads the feed's raw state under its lock and returns Cues.
"""

import bisect
import math
import random
from dataclasses import dataclass, field

from services.jb_lights import accent
from services.jb_lights.show import plan as planner
from services.jb_lights.show import styles
from services.jb_lights.show.library import Library

SETTLE_S = 15.0           # a song's plan may change while its feel settles; then it holds
PEAK_HOLD_S = 12.0        # how long a drop with no section after it keeps the peak look
SECTION_PEAK = 0.55       # an unlabeled section this much "up" (and loud) is a chorus-like lift
DRUM_ON = 0.5             # a drum reading this strong is a hit for the lights
PULSE_MIN_S = 1 / 3       # whole-area pulses at most 3 a second (flash safety)
MAX_FLASHES_PER_S = 3
FLASH_S = 0.45            # a white flash fades over this long
BLACK_MAX_S = 1.5         # a blackout never lasts longer than this
BURST_MIN_S = 1.0         # a burst (a drop, a big hit) at most once a second...
DROP_MIN_S = 0.4          # ...but a drop may follow a big hit this soon (still under 3 a second)
BURST_HIT = 0.8           # a hit this strong bursts on its own
TENSION_S = 8.0           # a riser reaches full tension over this long
LEVEL_TAU_S = 3.0         # "how loud is it lately"
DWELL_BARS = 4            # before any section: a look for the sound holds at least this long
BAR_TURN_BARS = 16        # a section this long: the level bar turns to another of its scenes
SPLASH_S = {"splash": 0.9, "shimmer": 1.8}   # a crash's splash lasts this long
SPLASH_GAP_S = 0.25       # one splash at most this often
SLAM_BEATS = 1.5          # a slam settles over this many beats
SWELL_IN_S, SWELL_OUT_S = 0.7, 2.5
LEAD_S = 0.05             # with a map: looks and moments this early (the capture's and a frame's delay)
LATE_S = {"gap": 0.1, "hit": 0.02, "drop": 0.0}   # how late the analyzer calls each one
PRE_BARS = 2              # with a map: the build into a big section starts this many bars before it
TWIN_S = 0.8              # a live moment this close to the map's own is the same one
W_IN, H_IN = accent.UPPER_WIDTH_IN, accent.UPPER_WIDTH_IN / accent.UPPER_ASPECT
CENTRE_IN = (0.5 * W_IN, 15.0)                     # the middle of the lit cabinet (arena's too)
WHITE = (255.0, 255.0, 255.0)


@dataclass
class Cues:
    style: styles.Style
    palette: tuple
    tier: str
    scene: dict              # part -> scene name
    beat: float              # beats since the song started (whole part = count, fraction = phase)
    bar_beat: int            # 0..3
    tempo: float             # bpm (a gentle default when unknown)
    pulse: dict              # kick / snare / offbeat / hat / crash / drive: 0..1
    level: float             # the music right now, 0..1
    loud: float              # the music lately, 0..1
    bands: list
    black: float             # 0 normal .. 1 dark
    flash: float             # 0 .. 1 white
    tension: float           # 0 .. 1, a build rising
    cool: float              # 0 .. 1, a breakdown
    since_section: float     # seconds
    seed: int
    since_burst: float = 1e9  # seconds since a drop, a big hit or a lift (arena's white burst)
    character: str = "drive"  # the section's (plan.character)
    label: str | None = None  # its label (A, B, ...): the same music, the same look
    n: int = 1               # which time round
    move: float = 0.0        # movement steps: beats at the plan's rate
    rate: float = 1.0        # steps a beat
    smooth: float = 0.3      # 0 punchy .. 1 flowing
    crossfade: float = 0.8   # seconds a scene change takes (0: a cut)
    look_gain: float = 0.8   # where in the show's light range the look sits (0..1)
    lift: float = 0.0        # a slam or a swell lifting the brightness, 0..1
    splashes: list = field(default_factory=list)   # (age s, x in, y in, strength, colour, kind)
    ring: float = 0.0        # the cymbals' wash, 0..1
    plan: object = None      # the song's plan (plan.Plan)


class Director:
    def __init__(self, library=None):
        self.library = library or Library()      # every show, and the owner's choices
        self.song = None
        self.seed = 0
        self.plan = planner.Plan(styles.STYLES["pop"])
        self.style = self.plan.show
        self.locked = False
        self.planned = None                      # (feel, tempo, genre, tags) the plan was made from
        self.started = 0.0
        self.last = None
        self.beat = 0.0
        self.move = 0.0
        self.prev_phase = None
        self.pulse = {"kick": 0.0, "snare": 0.0, "offbeat": 0.0, "hat": 0.0, "crash": 0.0}
        self.pulse_at = {"kick": -1e9, "snare": -1e9, "offbeat": -1e9}
        self.seen = {}
        self.flashes = []
        self.flash = 0.0
        self.black_since = None
        self.black = 0.0
        self.tension = 0.0
        self.cool = 0.0
        self.loud = 0.5
        self.section_at = 0.0
        self.burst_at = -1e9
        self.rnd = random.Random(7)
        self._reset_song()

    def _reset_song(self):
        self.section_look = None                 # the look of the section we're in
        self.drop_look, self.drop_until = None, -1e9
        self.sound_look, self.sound_since = None, -1e9
        self.break_look = None
        self.current, self.look_at, self.look_beat = None, 0.0, 0.0
        self.cut = False
        pal = self.plan.palette
        self.pal_from = self.pal_to = pal
        self.pal_at, self.pal_s = -1e9, 0.0
        self.splashes, self.last_splash = [], -1e9
        self.lift_kind, self.lift_at, self.lift_v = None, -1e9, 0.0
        self.map_key, self.map_starts, self.map_moments = None, [], []
        self.map_index, self.map_next, self.map_pos = None, 0, None
        self.map_fired = {}
        self.pre = 0.0

    # The song and its plan ---------------------------------------------------------

    def _new_song(self, raw, now):
        self.song = (raw.title, raw.artist)
        self.seed = styles.seed(raw.title, raw.artist)
        self.started = self.section_at = now
        self.locked = False
        self.beat = self.move = 0.0
        self.prev_phase = None
        self._make_plan(raw)
        self._reset_song()

    def _make_plan(self, raw):
        feel = raw.feel if isinstance(raw.feel, dict) else None
        choose = getattr(self.library, "choose", None)
        if choose is not None:
            show, why = choose(feel, raw.genre, raw.tags, raw.arousal, self.seed)
        else:
            show, why = self.library.pick(raw.genre, raw.tags, raw.arousal, self.seed), []
        self.plan = planner.Plan(show, feel or {}, raw.tempo, self.seed, why)
        self.style = show
        self.planned = (feel, raw.tempo, raw.genre, tuple(raw.tags or ()))
        # the looks so far, again in the new plan (a new show, its own colours)
        if self.section_look is not None:
            self.section_look = self.plan.look(self.section_look_from)
        self.drop_look = self.sound_look = self.break_look = None

    def _replan_if_needed(self, raw, now):
        if self.locked:
            return
        feel = raw.feel if isinstance(raw.feel, dict) else None
        if raw.known and feel:
            self.locked = True                   # a learned song: its feel is the whole song's
        elif now - self.started > SETTLE_S:
            self.locked = True
        old = self.planned
        tempo_moved = raw.tempo and (not old[1] or abs(raw.tempo - old[1]) > 8)
        if feel != old[0] or tempo_moved or (raw.genre, tuple(raw.tags or ())) != old[2:]:
            self._make_plan(raw)

    # Moments -------------------------------------------------------------------------

    def _fresh(self, name, at):
        """True once for each new event of this kind."""
        if at is None or at <= self.seen.get(name, -1e9):
            return False
        self.seen[name] = at
        return True

    def _flash(self, now, amount):
        self.flashes = [t for t in self.flashes if now - t < 1.0]
        if not self.plan.flash or len(self.flashes) >= MAX_FLASHES_PER_S:
            return
        self.flashes.append(now)
        self.flash = max(self.flash, amount)

    def _burst(self, now, spacing=BURST_MIN_S):
        if now - self.burst_at >= spacing:
            self.burst_at = now

    def _lift(self, now, kind, v):
        if v > self._lift_now(now):
            self.lift_kind, self.lift_at, self.lift_v = kind, now, v

    def _lift_now(self, now) -> float:
        a = now - self.lift_at
        if self.lift_kind == "slam":
            return self.lift_v * math.exp(-a / (SLAM_BEATS * 60 / (self.plan.tempo or 100)))
        if self.lift_kind == "swell":
            if a < SWELL_IN_S:
                k = a / SWELL_IN_S
                return self.lift_v * k * k * (3 - 2 * k)
            return self.lift_v * math.exp(-(a - SWELL_IN_S) / SWELL_OUT_S)
        return 0.0

    def _accent(self, now, v, drop):
        """The drop, or the hit after a gap (drop False: a big hit on its own)."""
        kind = self.plan.accents["drop"]
        if kind == "flash":
            self._flash(now, v)
            self._lift(now, "slam", 0.5 * v)
        elif kind == "slam":
            self._lift(now, "slam", v)
        elif kind == "swell":
            self._lift(now, "swell", 0.7 * v)
        if drop and kind != "swell":
            self._burst(now, DROP_MIN_S)

    def _moment(self, kind, now, raw, strength=None, after_gap=None):
        plan = self.plan
        if kind == "drop":
            self.pulse["crash"] = 1.0
            self._accent(now, 1.0, drop=True)
            self.black_since, self.black = None, 0.0     # the drop is the reveal: no fade up
            if self.map_index is None:                    # (a map says what the drop's section is)
                self.drop_look = plan.look({"role": "drop", "label": None})
                self.drop_until = now + PEAK_HOLD_S
                self.cut = plan.accents["drop"] in ("slam", "flash", "burst")
        elif kind == "gap":
            if plan.accents["gap"]:
                self.black_since = now
        elif kind == "hit":
            strength = raw.hit_strength if strength is None else strength
            after_gap = raw.hit_after_gap if after_gap is None else after_gap
            self.pulse["crash"] = 1.0
            if self.black_since is not None or after_gap:
                self._accent(now, 1.0, drop=False)
            elif strength >= 0.6 and plan.show.strobe >= 1:
                self._accent(now, strength, drop=False)
            if strength >= BURST_HIT and not after_gap and raw.build != "rising":
                self._burst(now)
            self.black_since, self.black = None, 0.0

    def _crash(self, now, v):
        """A crash cymbal: a splash of light spreading from where it struck (a
        flash too, in shows that flash on it; a shimmer in a ballad or a quiet part)."""
        kind = self.plan.accents["crash"]
        if kind == "none":
            return
        ch = self.current.character if self.current else "drive"
        if kind in ("splash", "flash") and ch in ("quiet", "vocal", "break") and v < 0.8:
            kind = "shimmer"
        if kind == "flash":
            self._flash(now, 0.5 * v)
            kind = "splash"
        if now - self.last_splash < SPLASH_GAP_S:
            return
        self.last_splash = now
        if ch in ("big", "solo"):
            x, y = CENTRE_IN
        else:
            x, y = self.rnd.uniform(0.2, 0.8) * W_IN, self.rnd.uniform(0.25, 0.75) * H_IN
        pal = self.pal_to
        light = max(pal, key=sum)
        colour = planner.lerp(light, WHITE, 0.45 + 0.35 * planner._num(self.plan.feel.get("aggressive"), 0.0))
        self.splashes.append((now, x, y, v * (0.6 + 0.4 * (self.current.gain if self.current else 0.8)), colour,
                              kind))

    # The map ---------------------------------------------------------------------------

    def _map_now(self, raw, now):
        """A learned song locked to its map: (its sections, where in the song it is), or (None, None)."""
        m = raw.map
        if not (m and m.get("known") and raw.synced and m.get("song") == raw.song and raw.pos is not None
                and now - raw.pos_at < 1.0):
            return None, None
        if raw.map_at != self.map_key:
            self.map_key = raw.map_at
            secs = [s for s in m.get("sections") or [] if isinstance(s.get("start"), (int, float))]
            self.map_secs = secs
            self.map_starts = [float(s["start"]) for s in secs]
            moms = [e for e in m.get("moments") or [] if e.get("type") in LATE_S and isinstance(e.get("t"), (int, float))]
            self.map_moments = sorted(moms, key=lambda e: e["t"])
            self.map_index, self.map_pos = None, None
        return self.map_secs, raw.song_pos(now)

    def _due(self, e) -> float:
        return float(e["t"]) - LATE_S[e["type"]] - LEAD_S

    def _follow_map(self, raw, now, secs, pos):
        k = bisect.bisect_right(self.map_starts, pos + LEAD_S) - 1
        if k >= 0 and k != self.map_index:
            self.map_index = k
            self._section(secs[k], now)
        # the moments coming due (after a seek: only those from here on)
        moms = self.map_moments
        if self.map_pos is None or pos < self.map_pos - 0.5 or pos > self.map_pos + 2.0:
            self.map_next = next((i for i, e in enumerate(moms) if self._due(e) > pos), len(moms))
        while self.map_next < len(moms) and self._due(moms[self.map_next]) <= pos:
            e = moms[self.map_next]
            self.map_next += 1
            self.map_fired[e["type"]] = now
            self._moment(e["type"], now, raw, e.get("strength", 0.8), e.get("after_gap", False))
        self.map_pos = pos
        # the bars before a big section: the build into it
        self.pre = 0.0
        if 0 <= k < len(secs) - 1 and planner.character(secs[k + 1]) == "big" \
                and planner.character(secs[k]) != "big":
            left = self.map_starts[k + 1] - pos
            span = PRE_BARS * 240 / (self.plan.tempo or raw.tempo or 100)
            if 0 < left < span:
                self.pre = 1 - left / span

    def _twin(self, kind, now, pos) -> bool:
        """A live moment the map has just played, or is about to."""
        if now - self.map_fired.get(kind, -1e9) < TWIN_S:
            return True
        return any(e["type"] == kind and 0 <= self._due(e) - pos <= TWIN_S for e in self.map_moments)

    # Looks -----------------------------------------------------------------------------

    def _section(self, s, now):
        """A new section (a section event, or the map's), or a correction to the last one."""
        self.section_at = now
        s = dict(s or {})
        if not s.get("label") and not s.get("role"):
            # a plain "the song moved on" (no labels): a new look, a lift if it went up and it's loud
            self._unlabeled = getattr(self, "_unlabeled", 0) + 1
            s["label"] = f"~{self._unlabeled}"
            up = s.get("energy") == "up" and self.loud > SECTION_PEAK
            s.update(role="chorus" if up else "verse", size=0.9 if up else self.loud)
            if s.get("energy") == "down":
                self.drop_until = min(self.drop_until, now)
        look = self.plan.look(s)
        held = self.drop_look is not None and now < self.drop_until
        if held and look.character == "big":
            # the drop's look carries on, now with this section's label
            self.drop_look.label = look.label
            if look.label:
                self.plan.looks[look.label] = self.drop_look
            self.section_look, self.section_look_from = self.drop_look, s
            return
        self.drop_until = -1e9
        self.section_look, self.section_look_from = look, s
        self.cut = look.character == "big" and self.plan.accents["drop"] == "slam" and not self.plan.gentle

    def _sound(self, raw, now):
        """Before any section: a look for how the music sounds, held a few bars."""
        if self.loud < 0.35 or (raw.arousal is not None and raw.arousal < 0.3):
            ch = "quiet"
        elif raw.voice >= planner.VOICED:
            ch = "vocal"
        else:
            ch = "drive"
        bar_s = 240 / (raw.tempo or 100)
        if self.sound_look is None or (ch != self.sound_look.character and now - self.sound_since > DWELL_BARS * bar_s):
            self.sound_look, self.sound_since = self.plan.look(None, ch), now
        return self.sound_look

    def _look(self, raw, now):
        if self.drop_look is not None and now < self.drop_until:
            return self.drop_look
        if raw.build == "breakdown":
            if self.break_look is None:
                self.break_look = self.plan.look(None, "break")
            return self.break_look
        return self.section_look or self._sound(raw, now)

    def _palette(self, now):
        if self.pal_s <= 0 or now - self.pal_at >= self.pal_s:
            return self.pal_to
        return planner.blend(self.pal_from, self.pal_to, (now - self.pal_at) / self.pal_s)

    def _show(self, look, now, cut):
        if look is self.current:
            return
        before = self._palette(now)
        self.current, self.look_at, self.look_beat = look, now, self.beat
        self.pal_from, self.pal_to, self.pal_at = before, look.palette, now
        self.pal_s = 0.0 if cut else self.plan.crossfade
        self.look_cut = cut

    def report(self, now, raw) -> dict | None:
        """For admin › Light Show › Now: the song's plan, the look it's in, and
        its map (a learned song's whole map, or the sections heard so far)."""
        look = self.current
        if self.song is None or look is None:
            return None
        out = {"title": self.song[0], "artist": self.song[1], "plan": self.plan.summary(), "settled": self.locked,
               "look": {"character": look.character, "label": look.label, "n": look.n, "scene": dict(look.scene)}}
        m = raw.map
        if m and m.get("song") == raw.song and raw.song is not None:
            secs = [s for s in m.get("sections") or [] if isinstance(s, dict) and isinstance(s.get("start"), (int, float))]
            out["map"] = {"known": bool(m.get("known")), "following": self.map_index is not None,
                          "pos": raw.song_pos(now), "synced": raw.synced,
                          "sections": [{"start": round(float(s["start"]), 2), "end": s.get("end"),
                                        "label": s.get("label"), "role": s.get("role"),
                                        "character": planner.character(s)} for s in secs[:80]]}
        return out

    # Every frame -----------------------------------------------------------------------

    def update(self, now, raw) -> Cues:
        dt = 0.0 if self.last is None else max(0.0, min(0.1, now - self.last))
        self.last = now
        # A new song, or what it is coming in: (re)plan.
        if (raw.title, raw.artist) != self.song:
            self._new_song(raw, now)
        else:
            self._replan_if_needed(raw, now)
        plan, st = self.plan, self.plan.show

        # The beat: the analyzer's clock, carried forward between frames; the movement at the plan's rate.
        tempo = raw.tempo or 100.0
        before = self.beat
        if raw.phase is not None:
            phase = (raw.phase + (now - raw.phase_at) * tempo / 60) % 1.0
            if self.prev_phase is not None:
                step = (phase - self.prev_phase) % 1.0
                self.beat += step if step < 0.5 else 0.0          # never run backwards when it's pulled
            self.prev_phase = phase
            bar_beat = (raw.bar_beat + int(raw.phase + (now - raw.phase_at) * tempo / 60)) % 4
        else:
            self.beat += dt * tempo / 60
            self.prev_phase = None
            bar_beat = int(self.beat) % 4
        speed = 1 if plan.gentle else 4 if self.tension > 0.85 else 2 if self.tension > 0.5 else 1
        self.move += (self.beat - before) * plan.rate * speed

        # Drums: each one a pulse that fades at the plan's release; the crash rings on.
        fade = math.exp(-dt / max(plan.release, 0.05))
        for name in self.pulse:
            self.pulse[name] *= fade if name != "crash" else math.exp(-dt / 0.5)
        for name in ("kick", "snare", "hat"):
            if self._fresh(name, raw.drum_at.get(name)):
                if name == "hat" or now - self.pulse_at[name] >= PULSE_MIN_S:
                    self.pulse[name] = max(self.pulse[name], raw.drum_v.get(name, 1.0))
                    if name != "hat":
                        self.pulse_at[name] = now
        if self._fresh("beat", raw.beat_at) and now - self.pulse_at["kick"] >= PULSE_MIN_S:
            self.pulse["kick"], self.pulse_at["kick"] = 1.0, now
        frac = self.beat % 1.0
        if 0.45 < frac < 0.6 and now - self.pulse_at["offbeat"] >= PULSE_MIN_S:
            self.pulse["offbeat"], self.pulse_at["offbeat"] = 1.0, now
        if self._fresh("crash", raw.crash_at):
            self.pulse["crash"] = max(self.pulse["crash"], raw.crash_v)
            self._crash(now, raw.crash_v)
        drive = raw.level if plan.drive == "level" or not plan.beat_locked else self.pulse[plan.drive]

        # Loudness lately, the build's tension, the breakdown's cool.
        self.loud += (raw.level - self.loud) * min(1.0, dt / LEVEL_TAU_S)
        rising, breakdown = raw.build == "rising", raw.build == "breakdown"
        self.tension = min(1.0, self.tension + dt / TENSION_S) if rising else max(0.0, self.tension - dt * 2)
        self.cool = min(1.0, self.cool + dt / 1.5) if breakdown else max(0.0, self.cool - dt / 0.5)
        self.flash *= math.exp(-dt / (FLASH_S / 3))

        # The song's map, when it's learned and locked to it: sections and moments on time.
        secs, pos = self._map_now(raw, now)
        if secs is not None:
            self._follow_map(raw, now, secs, pos)
        else:
            self.map_index, self.pre = None, 0.0
        if self._fresh("section", raw.section_at) and secs is None:
            self._section(raw.section, now)
        for kind in ("drop", "gap", "hit"):
            if self._fresh(kind, getattr(raw, f"{kind}_at")) and not (secs is not None and self._twin(kind, now, pos)):
                self._moment(kind, now, raw)
        if self.black_since is not None and (now - self.black_since > BLACK_MAX_S
                                             or (now - self.black_since > 0.2 and raw.level > 0.6)):
            self.black_since = None
        target = 0.0 if self.black_since is None else (1.0 if plan.accents["gap"] == "blackout" else 0.7)
        self.black = target if target > self.black else max(target, self.black - dt / 0.15)
        tension = self.tension if plan.gentle else max(self.tension, 0.8 * self.pre)

        # The look, its scenes and colours.
        look = self._look(raw, now)
        self._show(look, now, self.cut)
        self.cut = False
        scene = dict(look.scene)
        turn = int((self.beat - self.look_beat) // (4 * BAR_TURN_BARS))
        if turn:
            opts = st.scenes_for("bar", look.character)
            k = opts.index(scene["bar"]) if scene["bar"] in opts else 0
            scene["bar"] = opts[(k + turn) % len(opts)]
        gain = look.gain * (1 - 0.2 * self.pre if plan.gentle else 1.0)
        self.splashes = [s for s in self.splashes if now - s[0] < SPLASH_S[s[5]]]
        return Cues(style=st, palette=self._palette(now), tier=look.tier, scene=scene, beat=self.beat,
                    bar_beat=bar_beat, tempo=tempo, pulse={**self.pulse, "drive": drive}, level=raw.level,
                    loud=self.loud, bands=raw.bands, black=self.black, flash=self.flash, tension=tension,
                    cool=self.cool, since_section=now - self.section_at, seed=self.seed,
                    since_burst=now - self.burst_at, character=look.character, label=look.label, n=look.n,
                    move=self.move, rate=plan.rate, smooth=plan.smooth,
                    crossfade=0.0 if self.look_cut else plan.crossfade, look_gain=gain, lift=self._lift_now(now),
                    splashes=[(now - s[0], *s[1:]) for s in self.splashes], ring=raw.ring, plan=plan)
