"""The lights' view of the music: jb-analyzer's audio-analysis stream
(docs/contracts/audio-analysis.md, v1), plus which look to show.

One AudioFeed is shared by the accent chain and the level bar (each runs in
its own thread), so both always agree on the look:

  idle    nothing playing for a few seconds -> the idle looks (city, white bar)
  eq      livelier music (mood arousal high) -> graphic EQ / classic analyzer
  ribbon  calmer music (arousal low) -> the flowing ribbon, breathing with it

The look follows the mood for the first seconds of the music, then only changes when the mood clearly
moves (hysteresis) and has held for a while, so a song doesn't flip-flop;
callers crossfade between looks. PLAYING_LOOK pins one look instead.

Lights must never depend on the analyzer to work: with it missing or silent,
everything stays idle.
"""

import json
import threading
import time

import paho.mqtt.client as mqtt

TOPIC = "jukebox/analysis/audio"
N_BANDS = 32

PLAYING_LOOK = "auto"     # "auto" (by mood), "eq" or "ribbon"
IDLE_AFTER_S = 4.0        # no sound this long -> back to idle (rides over gaps between songs)
EQ_ABOVE = 0.55           # arousal: ribbon -> eq above this
RIBBON_BELOW = 0.40       # arousal: eq -> ribbon below this
START_SPLIT = 0.48        # first pick of a song: eq at or above this
MIN_DWELL_S = 30.0        # a look stays at least this long before the mood may change it
SETTLE_S = 12.0           # early in a song the mood is still settling (its window holds the last song):
                          # follow it directly until then


class LookPicker:
    """Pure: given the time, whether sound is present, and arousal -> a look."""

    def __init__(self, mode=PLAYING_LOOK):
        self.mode = mode
        self.look = "idle"
        self.since = 0.0
        self.last_sound = -1e9
        self.play_start = 0.0

    def update(self, now: float, sounding: bool, arousal: float | None) -> str:
        if sounding:
            self.last_sound = now
        playing = now - self.last_sound < IDLE_AFTER_S
        if not playing:
            want = "idle"
        elif self.mode in ("eq", "ribbon"):
            want = self.mode
        elif self.look == "idle" or now - self.play_start < SETTLE_S:
            if self.look == "idle":
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

    def __init__(self, bands, level, beat_age, drop_age, arousal, look):
        self.bands = bands          # 32 values 0..1, bass first
        self.level = level          # 0..1
        self.beat_age = beat_age    # seconds since the last beat
        self.drop_age = drop_age    # seconds since the last drop
        self.arousal = arousal      # 0..1 or None
        self.look = look            # idle | eq | ribbon

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


class AudioFeed:
    def __init__(self, host="localhost", picker=None):
        self.picker = picker or LookPicker()
        self._lock = threading.Lock()
        self._bands = [0.0] * N_BANDS
        self._level = 0.0
        self._last_fast = -1e9
        self._last_beat = -1e9
        self._last_drop = -1e9
        self._arousal = None
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
        with self._lock:
            if kind == "fast":
                bands = data.get("bands")
                if isinstance(bands, list) and len(bands) == N_BANDS:
                    self._bands = [max(0.0, min(1.0, float(v))) for v in bands]
                self._level = max(0.0, min(1.0, float(data.get("level", 0.0))))
                self._last_fast = now
                if data.get("beat"):
                    self._last_beat = now
            elif kind == "slow":
                mood = data.get("mood") or {}
                a = mood.get("arousal")
                self._arousal = float(a) if isinstance(a, (int, float)) and data.get("playing") else self._arousal
            elif kind == "events":
                if data.get("type") == "drop":
                    self._last_drop = now
            elif kind == "status" and not data.get("online"):
                self._last_fast = -1e9

    def snapshot(self, now=None) -> Snapshot:
        now = time.monotonic() if now is None else now
        with self._lock:
            sounding = now - self._last_fast < 0.5          # fast frames only flow while there's sound
            look = self.picker.update(now, sounding, self._arousal)
            fresh = sounding
            return Snapshot(
                bands=list(self._bands) if fresh else [0.0] * N_BANDS,
                level=self._level if fresh else 0.0,
                beat_age=now - self._last_beat,
                drop_age=now - self._last_drop,
                arousal=self._arousal,
                look=look,
            )
