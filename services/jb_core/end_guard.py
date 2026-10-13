"""Stopping cleanly after the last song.

When a song ends, iOS moves straight on to the next song in whatever list
it was started from -- over AVRCP there's no "stop after this one". If the
jukebox's queue is empty that next song was never chosen, and reacting after
the fact means a blip of it plays. So while the queue is empty, jb-core
keeps a deadline at the playing song's end (from the phone's reported
position and duration) and stops it a moment before -- the phone pauses on
the last song, now-playing clears, and nothing new starts.

Pure: feed it now-playing updates and the time; ask it whether to stop.
"""

END_MARGIN_S = 0.5        # stop this long before the end (the tail of a song is near silent)
STALE_S = 5.0             # a position report older than this isn't trusted to time the end
MIN_DURATION_S = 10.0     # don't guard anything this short (bad metadata)


class EndGuard:
    def __init__(self):
        self.deadline = None
        self.reported_at = None
        self.title = None
        self.fired_for = None

    def update(self, np: dict, now: float) -> None:
        """A now-playing report: re-time the end of the playing song."""
        title = np.get("title")
        if title != self.fired_for:
            self.fired_for = None
        duration, position = np.get("duration_ms"), np.get("position_ms")
        if (np.get("status") != "playing" or not title or not isinstance(duration, (int, float))
                or not isinstance(position, (int, float)) or duration < MIN_DURATION_S * 1000):
            self.deadline = None
            return
        self.title = title
        self.reported_at = now
        self.deadline = now + (duration - position) / 1000 - END_MARGIN_S

    def due(self, now: float, queue_empty: bool) -> str | None:
        """The title to stop, once, when the last song reaches its end."""
        if self.deadline is None or not queue_empty or self.fired_for == self.title:
            return None
        if now - self.reported_at > STALE_S + max(0.0, self.deadline - self.reported_at):
            return None                      # the reports stopped coming; don't act on old timing
        if now < self.deadline:
            return None
        self.fired_for, self.deadline = self.title, None
        return self.title
