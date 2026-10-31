"""The show's director: jb-analyzer's stream in, cues for every part out.

What it decides, moment to moment (docs/Light Show Styles.md):
  style    per song, from genre, tags or mood (library.pick: the owner's
           pin or the genre's rotation); the song's own
           seed picks among the style's palettes and scenes, so two songs
           of one genre still look different
  palette  moves on at each new section; drops and big choruses switch to
           the style's peak palette
  tier     calm (verses, breakdowns), main, peak (after a drop, or a
           section that lifts the energy) -- which scene each part plays
  scenes   one per part, rotating every ROTATE_BARS bars
  beat     where we are in the beat and the bar (the analyzer's beat
           clock, carried forward between its frames)
  drums    a pulse 0..1 for the kick, the snare, the off-beat and the
           hats, each decaying at the style's pace -- and a crash cymbal
           ringing on every hit, new section and drop
  black    a gap blacks the cabinet out (or dims it) until the hit
  flash    a big hit or a drop flashes white -- never more than
           MAX_FLASHES_PER_S (photosensitive safety: WCAG 2.3.1, Ofcom)
  burst    for styles that draw their own flash (arena): the drop itself,
           or a really big hit. Not a section lifting or the hit after a
           gap: those come just before the drop, so the flash would lead
           the beat (owner, 2026-10-30). At most one every BURST_MIN_S,
           but a drop may come sooner (never closer than DROP_MIN_S)
  tension  a build rising: the upper panel holds back and the rhythm
           tightens; cool: a breakdown thins everything out

Pure: update() reads the feed's raw state under its lock and returns Cues.
"""

import math
from dataclasses import dataclass

from services.jb_lights.show import styles
from services.jb_lights.show.library import Library

ROTATE_BARS = 16          # each part changes its movement this often, within a section...
BAR_ROTATE_BARS = 8       # ...the level bar, the cabinet's secondary voice, twice as often
PEAK_HOLD_S = 12.0        # how long a drop or a lift keeps the peak look
SECTION_PEAK = 0.55       # a section this much "up" (and loud) is a chorus-like lift
DRUM_ON = 0.5             # a drum reading this strong is a hit for the lights
PULSE_MIN_S = 1 / 3       # whole-area pulses at most 3 a second (flash safety)
MAX_FLASHES_PER_S = 3
FLASH_S = 0.45            # a white flash fades over this long
BLACK_MAX_S = 1.5         # a blackout never lasts longer than this
BURST_MIN_S = 1.0         # a burst (a drop, a big hit) at most once a second...
DROP_MIN_S = 0.4          # ...but a drop may follow a big hit this soon (still under 3 a second)
BURST_HIT = 0.8           # a hit this strong bursts on its own
TENSION_S = 8.0           # a riser reaches full tension over this long
LEVEL_TAU_S = 3.0         # "how loud is it lately", for the tier


@dataclass
class Cues:
    style: styles.Style
    palette: tuple
    tier: str
    scene: dict              # part -> scene name
    beat: float              # beats since the song started (whole part = count, fraction = phase)
    bar_beat: int            # 0..3
    tempo: float             # bpm (a gentle default when unknown)
    pulse: dict              # kick / snare / offbeat / hat / drive: 0..1
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


class Director:
    def __init__(self, library=None):
        self.library = library or Library()      # every show, and the owner's choices
        self.song = None
        self.style = styles.STYLES["pop"]
        self.seed = 0
        self.palette_n = 0
        self.peak_until = -1e9
        self.tier = "main"
        self.last = None
        self.beat = 0.0
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
        self.started = 0.0
        self.burst_at = -1e9

    def _new_song(self, raw, now):
        self.song = (raw.title, raw.artist)
        self.seed = styles.seed(raw.title, raw.artist)
        self.style = self.library.pick(raw.genre, raw.tags, raw.arousal, self.seed)
        self.palette_n = self.seed % len(self.style.palettes)
        self.peak_until = -1e9
        self.beat, self.prev_phase = 0.0, None
        self.section_at = self.started = now

    def _fresh(self, name, at):
        """True once for each new event of this kind."""
        if at is None or at <= self.seen.get(name, -1e9):
            return False
        self.seen[name] = at
        return True

    def _flash(self, now, amount):
        self.flashes = [t for t in self.flashes if now - t < 1.0]
        if self.style.strobe == 0 or len(self.flashes) >= MAX_FLASHES_PER_S:
            return
        self.flashes.append(now)
        self.flash = max(self.flash, amount)

    def _burst(self, now, spacing=BURST_MIN_S):
        if now - self.burst_at >= spacing:
            self.burst_at = now

    def update(self, now, raw) -> Cues:
        dt = 0.0 if self.last is None else max(0.0, min(0.1, now - self.last))
        self.last = now
        st = self.style
        # A new song, or its genre arriving a moment later: restyle.
        if (raw.title, raw.artist) != self.song:
            self._new_song(raw, now)
        elif raw.genre_at is not None and self._fresh("genre", raw.genre_at):
            style = self.library.pick(raw.genre, raw.tags, raw.arousal, self.seed)
            if style is not self.style:
                self.style = style
                self.palette_n = self.seed % len(style.palettes)
        st = self.style

        # The beat: the analyzer's clock, carried forward between frames.
        tempo = raw.tempo or 100.0
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

        # Drums: each one a pulse that fades at the style's pace.
        fade = math.exp(-dt / max(st.release, 0.05))
        for name in self.pulse:
            self.pulse[name] *= fade if name != "crash" else math.exp(-dt / 0.5)   # a cymbal rings on
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
        if st.drive == "level" or not st.beat_locked:
            drive = raw.level
        else:
            drive = self.pulse[st.drive]

        # Loudness lately (the tier), the build's tension, the breakdown's cool.
        self.loud += (raw.level - self.loud) * min(1.0, dt / LEVEL_TAU_S)
        rising, breakdown = raw.build == "rising", raw.build == "breakdown"
        self.tension = min(1.0, self.tension + dt / TENSION_S) if rising else max(0.0, self.tension - dt * 2)
        self.cool = min(1.0, self.cool + dt / 1.5) if breakdown else max(0.0, self.cool - dt / 0.5)

        self.flash *= math.exp(-dt / (FLASH_S / 3))

        # Moments.
        if self._fresh("section", raw.section_at):
            self.section_at = now
            self.pulse["crash"] = 1.0
            self.palette_n += 1
            if raw.section_energy == "up" and self.loud > SECTION_PEAK:
                self.peak_until = now + PEAK_HOLD_S
            elif raw.section_energy == "down":
                self.peak_until = min(self.peak_until, now)
        if self._fresh("drop", raw.drop_at):
            self.pulse["crash"] = 1.0
            self.peak_until = now + PEAK_HOLD_S
            self.palette_n += 1
            self._flash(now, 1.0)
            self._burst(now, DROP_MIN_S)
            self.black_since, self.black = None, 0.0     # the drop is the reveal: no fade up
        if self._fresh("gap", raw.gap_at) and st.gap:
            self.black_since = now
        if self._fresh("hit", raw.hit_at):
            self.pulse["crash"] = 1.0
            if self.black_since is not None or raw.hit_after_gap:
                self._flash(now, 1.0)
            elif raw.hit_strength >= 0.6 and st.strobe >= 1:
                self._flash(now, raw.hit_strength)
            if raw.hit_strength >= BURST_HIT and not raw.hit_after_gap and raw.build != "rising":
                self._burst(now)
            self.black_since, self.black = None, 0.0
        if self.black_since is not None and (now - self.black_since > BLACK_MAX_S
                                             or (now - self.black_since > 0.2 and raw.level > 0.6)):
            self.black_since = None
        target = 0.0 if self.black_since is None else (1.0 if st.gap == "blackout" else 0.7)
        self.black = target if target > self.black else max(target, self.black - dt / 0.15)

        # Tier and scenes.
        peak = now < self.peak_until
        if peak:
            tier = "peak"
        elif breakdown or self.loud < 0.35 or (raw.arousal is not None and raw.arousal < 0.3):
            tier = "calm"
        else:
            tier = "main"
        self.tier = tier
        scene = {}
        for k, part in enumerate(("upper", "foot", "bar")):
            options = getattr(st, part)[tier]
            rotation = int(self.beat // (4 * (BAR_ROTATE_BARS if part == "bar" else ROTATE_BARS)))
            scene[part] = options[(self.seed // (k + 1) + rotation + self.palette_n) % len(options)]
        scene["keys"] = st.keys
        palette = st.peak if peak else st.palettes[self.palette_n % len(st.palettes)]

        return Cues(style=st, palette=palette, tier=tier, scene=scene, beat=self.beat, bar_beat=bar_beat,
                    tempo=tempo, pulse={**self.pulse, "drive": drive}, level=raw.level, loud=self.loud,
                    bands=raw.bands, black=self.black, flash=self.flash, tension=self.tension,
                    cool=self.cool, since_section=now - self.section_at, seed=self.seed,
                    since_burst=now - self.burst_at)
