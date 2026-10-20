"""The pick doctor: notices a pick that never played, works out why, and
says what to do about it.

Every pick jb-core sends to a source is watched. It counts as played once
the source's now-playing shows that song playing; then, for a while
longer, the doctor listens for the sound actually reaching the cabinet
(jb-analyzer only streams while it hears something). If neither happens in
time, it checks what it knows, in order:

  no_controls  the phone is connected for audio only: it isn't letting the
               jukebox control it (its AVRCP session dropped)
  wrong_app    the phone is giving the jukebox another app (Messages, after a
               voice message) instead of Music: no song list, no playing.
               Only playing something in Music fixes it, not a reconnect
               (2026-10-19). Also said when the phone connects like that.
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
STUCK_AFTER = 3           # this many picks failing in a row, with nothing playing between: stop

MUSIC_APP = "Music"

ADVICE = {
    "wrong_app": ("Your phone is giving the jukebox {app}, not Music",
                  "Open the Music app on your phone and play any song for a moment. Your pick plays after."),
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
    "stuck": ("Songs aren't playing",
              "Shuffle is off. Turn Bluetooth off and on again on your phone, then pick a song."),
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
    noted: set = field(default_factory=set)   # notes already on the screen ("retrying", "wrong_app")


@dataclass
class PickDoctor:
    pick: Pick | None = None
    phone: dict = field(default_factory=dict)      # jb-source-phone/connected
    audio: dict = field(default_factory=dict)      # jb-audio/state
    sound_at: float | None = None                  # the last analyzer frame
    log: list = field(default_factory=list)
    in_row: int = 0                                # picks failed in a row (play-error), nothing playing between
    warned_app: str | None = None                  # the wrong app last warned about on connecting

    def connected(self, payload: dict) -> dict | None:
        """jb-source-phone/connected. A note for the screen when the phone
        connects presenting another app instead of Music (once per app)."""
        self.phone = payload
        if not wrong_app(payload):
            self.warned_app = None
            return None
        if payload.get("app") == self.warned_app:
            return None
        self.warned_app = payload.get("app")
        return self._note("wrong_app", {})

    def picked(self, track: dict, now: float):
        self.pick = Pick(track, now)

    def forget(self):
        """The pick is over one way or another (cancelled, the queue moved on)."""
        self.pick = None

    def heard(self, title: str | None, status: str | None, now: float):
        """A now-playing from the source the pick went to."""
        if status == "playing":
            self.in_row = 0
        p = self.pick
        if p and p.started_at is None and status == "playing" and title == p.track.get("title"):
            p.started_at = now

    def sound(self, now: float):
        self.sound_at = now

    def source_says(self, payload: dict, now: float) -> dict | None:
        """The source's own report on a pick (jb-source-phone/pick-problem):
        what went wrong, and whether it's healing it. Returns a note for the
        screen the first time it starts retrying, or straight away when the
        phone is presenting the wrong app."""
        p = self.pick
        if not p or payload.get("title") not in (None, p.track.get("title")):
            return None
        if payload.get("reason"):
            p.reason = payload["reason"]
        if payload.get("app"):
            self.phone = {**self.phone, "app": payload["app"], "browsable": False}
        if p.reason == "wrong_app":
            # The source keeps the pick until Music is back: say how, now.
            p.retrying_at = now
            if "wrong_app" in p.noted:
                return None
            p.noted.add("wrong_app")
            return self._note("wrong_app", p.track)
        if not payload.get("retrying"):
            return None
        p.retrying_at = now
        if "retrying" in p.noted:
            return None
        p.noted.add("retrying")
        if p.reason == "no_controls":
            headline, advice = "Reconnecting to your phone", "It stopped letting the jukebox control it. Your song will play in a moment."
        elif p.reason == "no_answer":
            headline, advice = "Trying that again", "Your phone was slow to answer. Your song will play in a moment."
        else:
            headline, advice = "Finding that song again", "Your phone's song list had changed. One moment."
        return {"title": p.track.get("title"), "artist": p.track.get("artist"), "problem": "retrying",
                "headline": headline, "advice": advice,
                "retry": False, "note": True}

    def failed(self, payload: dict, now: float) -> dict | None:
        """The source gave up on the pick (play-error): diagnose now."""
        self.in_row += 1
        p = self.pick
        if not p:
            return None
        if payload.get("reason"):
            p.reason = payload["reason"]
        return self._verdict(self._diagnose(started=False), now)

    @property
    def stuck(self) -> bool:
        """Picks keep failing (a phone that won't play anything, 2026-10-18:
        shuffle fed it 3,000 songs in two minutes). Stop advancing."""
        return self.in_row >= STUCK_AFTER

    def stuck_verdict(self) -> dict:
        self.in_row = 0
        self.pick = Pick({}, 0.0)
        return self._verdict("stuck", 0.0)

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
        if wrong_app(self.phone) or self.pick.reason == "wrong_app":
            return "wrong_app"
        if self.pick.reason in ADVICE:
            return self.pick.reason
        return "no_start"

    def _words(self, problem: str) -> tuple[str, str]:
        app = self.phone.get("app") or "another app"
        headline, advice = ADVICE[problem]
        return headline.format(app=app), advice.format(app=app)

    def _note(self, problem: str, track: dict) -> dict:
        headline, advice = self._words(problem)
        if problem == "wrong_app" and not track:      # on connecting: no pick to promise
            advice = "Open the Music app on your phone and play any song for a moment, so the jukebox can see your songs."
        return {"title": track.get("title"), "artist": track.get("artist"), "problem": problem,
                "headline": headline, "advice": advice, "retry": False, "note": True}

    def _verdict(self, problem: str, now: float) -> dict:
        title, advice = self._words(problem)
        verdict = {"title": self.pick.track.get("title"), "artist": self.pick.track.get("artist"),
                   "problem": problem, "headline": title, "advice": advice,
                   "retry": problem in RETRY_AFTER_RECONNECT, "track": self.pick.track}
        self.pick = None
        self.log = [{"at": round(time.time()), **{k: v for k, v in verdict.items() if k != "track"}},
                    *self.log][:LOG_LEN]
        return verdict


def wrong_app(phone: dict) -> bool:
    """Connected, with no song list, and presenting an app that isn't Music."""
    app = phone.get("app")
    return bool(phone.get("connected") and phone.get("browsable") is False and app and app != MUSIC_APP)
