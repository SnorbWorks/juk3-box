"""Holding Reset: cancel the song, or the reboot / shut-down menu.

The user's design (2026-10-12):
  - hold Reset (1 s) while a song plays -> that song is cancelled and the
    next in the queue plays (nothing queued: playback stops)
  - hold Reset with nothing playing -> the power menu
  - keep holding (4 s) while music was playing -> the song is cancelled at
    1 s, then the menu opens; playback is paused until a choice is made
  - in the menu (knob turns between Reboot, Shut down, Set up Wi-Fi, Cancel;
    click chooses): Reboot / Shut down clear the queue and take the system
    down; Set up Wi-Fi closes it and starts setup mode (the jukebox's own
    hotspot, docs/Setup Wizard — Plan.md); Cancel, a Reset click or
    MENU_TIMEOUT_S of nothing resume what was paused

A pure state machine: it's told what happened and returns what to do, as a
list of action names, so it can be tested without a bus or a phone.
  "skip"    cancel the playing song, play the next queued (or stop)
  "pause"   pause playback
  "open"    show the menu          "close"   hide it
  "resume"  play again what the menu paused
  "clear"   empty the queue
  "reboot" / "shutdown"
  "setup_wifi"  start setup mode
"""

MENU_TIMEOUT_S = 20
CHOICES = ("reboot", "shutdown", "wifi", "cancel")


class PowerMenu:
    def __init__(self):
        self.open = False
        self.opened_at = 0.0
        self.paused = False      # the menu paused a playing song

    def hold(self, t: float, playing: bool) -> list:
        if self.open:
            return []
        if playing:
            return ["skip"]
        return self._open(t, paused=False)

    def long_hold(self, t: float, playing: bool) -> list:
        """Still held at 4 s. If music was playing, the 1 s hold already
        cancelled the song and the next one may have started: pause it."""
        if self.open:
            return []
        return (["pause"] if playing else []) + self._open(t, paused=playing)

    def choose(self, choice: str) -> list:
        if not self.open or choice not in CHOICES:
            return []
        if choice == "cancel":
            return self._close()
        if choice == "wifi":
            return self._close() + ["setup_wifi"]
        self.open = False
        self.paused = False
        return ["close", "clear", choice]

    def reset_click(self) -> list:
        return self._close() if self.open else []

    def tick(self, t: float) -> list:
        if self.open and t - self.opened_at >= MENU_TIMEOUT_S:
            return self._close()
        return []

    @property
    def holding_queue(self) -> bool:
        """While the menu is open, nothing should start playing on its own."""
        return self.open

    def _open(self, t, paused):
        self.open, self.opened_at, self.paused = True, t, paused
        return ["open"]

    def _close(self):
        resume = self.paused
        self.open = self.paused = False
        return ["close"] + (["resume"] if resume else [])
