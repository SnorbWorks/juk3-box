"""Listening to one song: the pipeline every frame, the ears about once a
second, and the song's recording (memory.py) kept as it plays -- the same
for the live capture (main.py) and a song file (tools/learn_song.py).

  Listening.new_song(identity, t)   a new song: fresh pipeline state and recording
  Listening.frame(stereo, t)        one hop -> (fast payload or None, events)
  Listening.ears_due(t)             the ears want a listen (EARS_EVERY_S)
  Listening.heard(t, scores)        the ears' scores (from Ears.listen, maybe in
                                    a thread) -> their groups, recorded
  Listening.position(t, pos_s)      the source's song position, when it says

The recording notes the moments a study needs that the stream doesn't carry
as events: crash cymbals (at the moment they struck), the build state
changing, the tempo now and then.

Sections: while the song is new, LiveSections (sections.py) names them as
they come; they replace the fast layer's plain novelty events (moments.py),
which carry no labels. Pure: no I/O.
"""

from services.jb_analyzer import ears as ears_lib
from services.jb_analyzer.memory import Recording
from services.jb_analyzer.pipeline import Pipeline
from services.jb_analyzer.sections import LiveSections

EARS_EVERY_S = 0.96          # one ears window after another
TEMPO_EVERY_S = 10.0


class Listening:
    def __init__(self, rate: int, hop: int, ears: "ears_lib.Ears | None" = None):
        self.pipe = Pipeline(rate, hop)
        self.fps = self.pipe.fps
        self.ears = ears
        self.recording = None
        self.next_ears = 0.0
        self.next_tempo = 0.0
        self.build = "none"
        self.groups = None          # the ears' latest groups
        self.sections = LiveSections()

    def new_song(self, identity: dict, t: float):
        self.pipe.new_song()
        self.recording = Recording(identity, self.fps, t)
        self.build = "none"
        self.groups = None
        self.sections.reset()

    def frame(self, stereo, t: float) -> tuple:
        public, events = self.pipe.frame(stereo)
        f, rec = self.pipe.f, self.recording
        # the sections: live ones replace the plain novelty events
        events = [e for e in events if e["type"] != "section"]
        live = self.sections
        live.frame(f, t)
        for e in events:
            live.moment(e, t)
        if self.pipe.tex is not None:
            events += live.texture(self.pipe.tex, f["_rms_db"], t, self.pipe.slow.tempo)
        if self.ears is not None:
            self.ears.feed(stereo.mean(axis=1) if getattr(stereo, "ndim", 1) == 2 else stereo)
        if rec is not None:
            sounding = self.pipe.levels.sounding
            rec.frame(t, f, sounding)
            if self.pipe.tex is not None:
                rec.texture(t, self.pipe.tex, f["_rms_db"])
            for e in events:
                rec.event(t, e)
            if f["crash"] > 0:
                rec.event(t - f.get("crash_ago", 0.0), {"type": "crash", "strength": f["crash"]})
            build = public["build"] if public else "none"
            if build != self.build:
                rec.event(t, {"type": "build", "state": build})
                self.build = build
            if t >= self.next_tempo and self.pipe.slow.tempo:
                self.next_tempo = t + TEMPO_EVERY_S
                rec.event(t, {"type": "tempo", "tempo": round(self.pipe.slow.tempo, 2)})
        return public, events

    def ears_due(self, t: float) -> bool:
        if self.ears is None or not self.ears.ready() or t < self.next_ears:
            return False
        self.next_ears = t + EARS_EVERY_S
        return True

    def heard(self, t: float, scores: dict) -> dict:
        self.groups = ears_lib.hear(scores)
        self.sections.heard(self.groups)
        if self.recording is not None:
            self.recording.heard(t, self.groups)
        return self.groups

    def position(self, t: float, pos_s: float | None):
        if self.recording is not None:
            self.recording.position(t, pos_s)
