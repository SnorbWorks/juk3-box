"""The pick doctor: notices a pick that never played, works out why, and
says what to do about it.

Every pick jb-core sends to a source is watched. It counts as played once
the source's now-playing shows that song playing; then, for a while
longer, the doctor listens for the sound actually reaching the cabinet
(jb-analyzer only streams while it hears something). If neither happens in
time, it checks what it knows, in order:

  no_controls  the phone is connected for audio only: it isn't letting the
               jukebox control it (its AVRCP session dropped)
  no_answer    the phone didn't answer the play request
  not_found    the phone answered, but couldn't find the song
  no_start     the phone took the pick and never started it (another app
               holds the phone's audio)
  muted        the song plays, but the cabinet is muted or at 0
  no_sound     the song plays, but no sound reaches the cabinet (the phone
               is sending its audio somewhere else)

Each problem comes with advice for the screen (jukebox/jb-core/pick-problem).
The source tries to heal first (re-sync, reconnect): while it says it's
retrying, the doctor waits a little longer.

Pure (bar stamping its log with the clock): fed observations and the
time, it returns what it found.
"""

import time
from dataclasses import dataclass, field

START_S = 15.0            # a pick should be playing by now...
RETRY_GRACE_S = 100.0     # ...or by this long after the source says it's retrying
SOUND_S = 12.0            # once playing, sound should reach the cabinet by now
LOG_LEN = 20

ADVICE = {
    "no_controls": ("Your phone isn't letting the jukebox control it",
                    "Turn Bluetooth off and on again on your phone. Your song will play when it reconnects."),
    "no_answer": ("Your phone didn't answer",
                  "Turn Bluetooth off and on again on your phone. Your song will play when it reconnects."),
    "not_found": ("That song isn't on your phone",
                  "It may be in the cloud and not downloaded. Pick another song."),
    "no_start": ("Your phone didn't start the song",
                 "Open the Music app on your phone, then pick it again."),
    "muted": ("The jukebox is turned down",
              "Turn the volume knob up (press it to unmute)."),
    "no_sound": ("No sound is reaching the jukebox",
                 "Your phone is playing somewhere else. Check its audio output is set to the jukebox."),
}

# What makes it worth giving the pick another go once the phone is back.
RETRY_AFTER_RECONNECT = ("no_controls", "no_answer")


@dataclass
class Pick:
    track: dict
    at: float
    started_at: float | None = None
    retrying_at: float | None = None
    reason: str | None = None        # the source's own report (pick-problem)


@dataclass
class PickDoctor:
    pick: Pick | None = None
    phone: dict = field(default_factory=dict)      # jb-source-phone/connected
    audio: dict = field(default_factory=dict)      # jb-audio/state
    sound_at: float | None = None                  # the last analyzer frame
    log: list = field(default_factory=list)

    def picked(self, track: dict, now: float):
        self.pick = Pick(track, now)

    def forget(self):
        """The pick is over one way or another (cancelled, the queue moved on)."""
        self.pick = None

    def heard(self, title: str | None, status: str | None, now: float):
        """A now-playing from the source the pick went to."""
        p = self.pick
        if p and p.started_at is None and status == "playing" and title == p.track.get("title"):
            p.started_at = now

    def sound(self, now: float):
        self.sound_at = now

    def source_says(self, payload: dict, now: float) -> dict | None:
        """The source's own report on a pick (jb-source-phone/pick-problem):
        what went wrong, and whether it's healing it. Returns a note for the
        screen the first time it starts retrying."""
        p = self.pick
        if not p or payload.get("title") not in (None, p.track.get("title")):
            return None
        if payload.get("reason"):
            p.reason = payload["reason"]
        if not payload.get("retrying"):
            return None
        first = p.retrying_at is None
        p.retrying_at = now
        if not first:
            return None
        if p.reason == "no_controls":
            headline, advice = "Reconnecting to your phone", "It stopped letting the jukebox control it. Your song will play in a moment."
        elif p.reason == "no_answer":
            headline, advice = "Trying that again", "Your phone was slow to answer. Your song will play in a moment."
        else:
            headline, advice = "Finding that song again", "Your phone's song list had changed. One moment."
        return {"title": p.track.get("title"), "artist": p.track.get("artist"), "problem": "retrying",
                "headline": headline, "advice": advice,
                "retry": False}

    def failed(self, payload: dict, now: float) -> dict | None:
        """The source gave up on the pick (play-error): diagnose now."""
        p = self.pick
        if not p:
            return None
        if payload.get("reason"):
            p.reason = payload["reason"]
        return self._verdict(self._diagnose(started=False), now)

    def check(self, now: float) -> dict | None:
        """Called every second or so. A verdict once, when a pick has gone wrong."""
        p = self.pick
        if not p:
            return None
        if p.started_at is None:
            deadline = p.at + START_S
            if p.retrying_at is not None:
                deadline = max(deadline, p.retrying_at + RETRY_GRACE_S)
            if now < deadline:
                return None
            return self._verdict(self._diagnose(started=False), now)
        if self.sound_at is not None and self.sound_at >= p.started_at:
            self.pick = None                   # playing, and heard: all good
            return None
        if now < p.started_at + SOUND_S:
            return None
        return self._verdict(self._diagnose(started=True), now)

    def _diagnose(self, started: bool) -> str:
        if started:
            if self.audio.get("muted") or (self.audio.get("volume") is not None and self.audio["volume"] < 0.01):
                return "muted"
            return "no_sound"
        if self.phone.get("connected") and self.phone.get("controls") is False:
            return "no_controls"
        if self.pick.reason in ADVICE:
            return self.pick.reason
        return "no_start"

    def _verdict(self, problem: str, now: float) -> dict:
        title, advice = ADVICE[problem]
        verdict = {"title": self.pick.track.get("title"), "artist": self.pick.track.get("artist"),
                   "problem": problem, "headline": title, "advice": advice,
                   "retry": problem in RETRY_AFTER_RECONNECT, "track": self.pick.track}
        self.pick = None
        self.log = [{"at": round(time.time()), **{k: v for k, v in verdict.items() if k != "track"}},
                    *self.log][:LOG_LEN]
        return verdict
