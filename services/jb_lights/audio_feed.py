"""The lights' view of the music: jb-analyzer's audio-analysis stream
(docs/contracts/audio-analysis.md, v1), plus which look to show.

One AudioFeed is shared by the accent chain and the level bar (each runs in
its own thread), so both always agree on the look:

  idle    nothing playing for a few seconds -> the flowing waves (the ribbon) for
          3 minutes, then the idle looks (city, striped bar) for 3, and round again
  show    music playing -> the light show: the director (show/director.py) turns
          the whole stream -- drums, the beat clock, gaps, hits, sections,
          builds and drops -- into cues, in a style picked for the song's
          genre (show/styles.py), and every part draws its own role
  eq      (PLAYING_LOOK "auto") livelier music -> graphic EQ / classic analyzer
  ribbon  (PLAYING_LOOK "auto") calmer music -> the flowing ribbon

With "auto" the look follows the mood for the first seconds of the music, then only changes when the mood clearly
moves (hysteresis) and has held for a while, so a song doesn't flip-flop;
callers crossfade between looks. PLAYING_LOOK pins one look instead.

Lights must never depend on the analyzer to work: with it missing or silent,
everything stays idle.
"""

import json
import threading
import time

import paho.mqtt.client as mqtt

from services.jb_lights.show.director import DRUM_ON, Director

TOPIC = "jukebox/analysis/audio"
N_BANDS = 32

PLAYING_LOOK = "show"     # "show" (the director), "auto" (eq or ribbon by mood), "eq" or "ribbon"
IDLE_AFTER_S = 4.0        # no sound this long -> back to idle (rides over gaps between songs)
EQ_ABOVE = 0.55           # arousal: ribbon -> eq above this
RIBBON_BELOW = 0.40       # arousal: eq -> ribbon below this
START_SPLIT = 0.48        # first pick of a song: eq at or above this
MIN_DWELL_S = 30.0        # a look stays at least this long before the mood may change it
SETTLE_S = 12.0           # early in a song the mood is still settling (its window holds the last song):
                          # follow it directly until then
IDLE_WAVES_S = 180.0      # idle: the whole cabinet starts on the waves for this long (user, 2026-10-19)...
IDLE_CITY_S = 180.0       # ...then the idle looks for this long, and round again
WAVES_LOOK = "ribbon"


class LookPicker:
    """Pure: given the time, whether sound is present, and arousal -> a look."""

    def __init__(self, mode=PLAYING_LOOK):
        self.mode = mode
        self.look = "idle"
        self.since = 0.0
        self.last_sound = -1e9
        self.play_start = 0.0
        self.playing = False
        self.quiet_since = None

    def update(self, now: float, sounding: bool, arousal: float | None) -> str:
        if sounding:
            self.last_sound = now
        playing = now - self.last_sound < IDLE_AFTER_S
        started, self.playing = playing and not self.playing, playing
        if playing:
            self.quiet_since = None
        elif self.quiet_since is None:
            self.quiet_since = now
        if not playing:
            phase = (now - self.quiet_since) % (IDLE_WAVES_S + IDLE_CITY_S)
            want = WAVES_LOOK if phase < IDLE_WAVES_S else "idle"
        elif self.mode in ("show", "eq", "ribbon"):
            want = self.mode
        elif started or now - self.play_start < SETTLE_S:
            if started:
                self.play_start = now
            want = "eq" if arousal is None or arousal >= START_SPLIT else "ribbon"
        elif now - self.since < MIN_DWELL_S or arousal is None:
            want = self.look
        elif self.look == "eq" and arousal < RIBBON_BELOW:
            want = "ribbon"
        elif self.look == "ribbon" and arousal > EQ_ABOVE:
            want = "eq"
        else:
            want = self.look
        if want != self.look:
            self.look, self.since = want, now
        return self.look


class Snapshot:
    """One moment of the music, as the themes want it."""

    def __init__(self, bands, level, beat_age, drop_age, arousal, look, cues=None):
        self.bands = bands          # 32 values 0..1, bass first
        self.level = level          # 0..1
        self.beat_age = beat_age    # seconds since the last beat
        self.drop_age = drop_age    # seconds since the last drop
        self.arousal = arousal      # 0..1 or None
        self.look = look            # idle | show | eq | ribbon
        self.cues = cues            # the director's cues (show/director.py) while the show plays

    def resample(self, n: int) -> list:
        """The 32 bands as n columns (averaged down, or interpolated up)."""
        b = self.bands
        if n <= len(b):
            out = []
            for k in range(n):
                lo, hi = k * len(b) // n, max(k * len(b) // n + 1, (k + 1) * len(b) // n)
                out.append(sum(b[lo:hi]) / (hi - lo))
            return out
        out = []
        for k in range(n):
            x = k * (len(b) - 1) / (n - 1)
            i = min(int(x), len(b) - 2)
            out.append(b[i] + (b[i + 1] - b[i]) * (x - i))
        return out

    def beat(self, decay_s=0.15) -> float:
        """1 on a beat, fading to 0 over decay_s."""
        return max(0.0, 1 - self.beat_age / decay_s)


def _num(v, default=None):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


class Raw:
    """Everything the stream has said lately, as the director reads it
    (times are this process's monotonic clock, when each thing arrived)."""

    def __init__(self):
        self.level, self.bands = 0.0, [0.0] * N_BANDS
        self.build = "none"
        self.phase, self.phase_at, self.bar_beat = None, 0.0, 0
        self.tempo, self.arousal = None, None
        self.title = self.artist = self.genre = None
        self.tags, self.genre_at = [], None
        self.beat_at = None
        self.drum_at, self.drum_v = {}, {}
        self.gap_at = self.hit_at = self.section_at = self.drop_at = None
        self.hit_strength, self.hit_after_gap = 0.0, False
        self.section_energy = "same"


class AudioFeed:
    def __init__(self, host="localhost", picker=None, director=None):
        self.picker = picker or LookPicker()
        self.director = director or Director()
        self.raw = Raw()
        self._lock = threading.Lock()
        self._last_fast = -1e9
        self._last_beat = -1e9
        self._last_drop = -1e9
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = lambda c, u, f, rc, p=None: c.subscribe(f"{TOPIC}/#")
        self._client.on_message = self._on_message
        self._host = host

    def start(self):
        self._client.connect_async(self._host, 1883)
        self._client.loop_start()
        return self

    def stop(self):
        self._client.loop_stop()

    def _on_message(self, client, userdata, msg):
        # A malformed message is dropped, never allowed to kill paho's thread
        # (the lights would silently stop following the music).
        try:
            data = json.loads(msg.payload)
            if isinstance(data, dict):
                self.on_message(msg.topic, data)
        except (ValueError, TypeError, AttributeError):
            pass

    def on_message(self, topic, data, now=None):
        now = time.monotonic() if now is None else now
        kind = topic.rsplit("/", 1)[-1]
        raw = self.raw
        with self._lock:
            if kind == "fast":
                bands = data.get("bands")
                if isinstance(bands, list) and len(bands) == N_BANDS:
                    raw.bands = [max(0.0, min(1.0, float(v))) for v in bands]
                raw.level = max(0.0, min(1.0, float(data.get("level", 0.0))))
                self._last_fast = now
                if data.get("beat"):
                    self._last_beat = raw.beat_at = now
                for name in ("kick", "snare", "hat"):
                    v = _num(data.get(name), 0.0)
                    if v >= DRUM_ON:
                        raw.drum_at[name], raw.drum_v[name] = now, min(1.0, v)
                if data.get("build") in ("none", "rising", "breakdown", "drop"):
                    raw.build = data["build"]
                phase = _num(data.get("phase"))
                raw.phase, raw.phase_at = (phase % 1.0 if phase is not None else None), now
                raw.bar_beat = int(_num(data.get("bar_beat"), 0)) % 4
            elif kind == "slow":
                mood = data.get("mood") or {}
                a = mood.get("arousal")
                raw.arousal = float(a) if isinstance(a, (int, float)) and data.get("playing") else raw.arousal
                raw.tempo = _num(data.get("tempo"))
                who = data.get("identity") or {}
                raw.title, raw.artist = who.get("title"), who.get("artist")
                genre, tags = data.get("genre"), data.get("tags")
                tags = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
                if (genre, tags) != (raw.genre, raw.tags):
                    raw.genre, raw.tags, raw.genre_at = genre, tags, now
            elif kind == "events":
                kind = data.get("type")
                if kind == "drop":
                    self._last_drop = raw.drop_at = now
                elif kind == "gap":
                    raw.gap_at = now
                elif kind == "hit":
                    raw.hit_at = now
                    raw.hit_strength = _num(data.get("strength"), 0.5)
                    raw.hit_after_gap = bool(data.get("after_gap"))
                elif kind == "section":
                    raw.section_at = now
                    raw.section_energy = data.get("energy") if data.get("energy") in ("up", "down") else "same"
            elif kind == "status" and not data.get("online"):
                self._last_fast = -1e9

    def snapshot(self, now=None) -> Snapshot:
        now = time.monotonic() if now is None else now
        with self._lock:
            sounding = now - self._last_fast < 0.5          # fast frames only flow while there's sound
            look = self.picker.update(now, sounding, self.raw.arousal)
            if not sounding:
                self.raw.level, self.raw.bands = 0.0, [0.0] * N_BANDS
            cues = self.director.update(now, self.raw) if look == "show" else None
            return Snapshot(
                bands=list(self.raw.bands),
                level=self.raw.level,
                beat_age=now - self._last_beat,
                drop_age=now - self._last_drop,
                arousal=self.raw.arousal,
                look=look,
                cues=cues,
            )
