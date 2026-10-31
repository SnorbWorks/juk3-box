"""The song now playing, from the analyzer's side: which song it is (the
sources' now-playing), whether the cabinet knows it (a map from an earlier
whole play), where in it the cabinet is, and learning it once it's over.

  first play  Listening (listening.py): the pipeline, the ears, the live
              sections, and the recording; when the song ends, a recording
              that heard it through goes to the study (main.py runs it)
  replay      the map (memory.Store) and the sync (sync.py): the position
              locked to the sound, and the map's sections as events on
              time, in place of the live ones

  Songs(rate, hop, ears=None)
  .now_playing(t, topic, msg) -> {"changed": bool, "finished": finish() or None}
  .learned(key, song_map, rec)   the map (and recording) for the song now playing
  .frame(t, stereo)           -> (fast payload or None, events); a section event's
                                 "start" (and "end") are in the song, "at" on the clock
  .heard(t, scores)           the ears' scores (main runs the model in a thread)
  .tagged(tags, genre)        listeners' tags (tags.py) for the song now playing
  .tick(t)                    -> finish() when the song has gone quiet a while
  .slow()                     the slow channel's song fields
  .map_payload()              the map topic: the learned map, or the live one so far
  .finish(t)                  -> {"key", "arrays" (worth studying, or None), "known",
                                  "coverage"}, or None with no song
Pure but for the time it's told.
"""

import numpy as np

from services.jb_analyzer import ears as ears_lib
from services.jb_analyzer.feel import feel as feel_of
from services.jb_analyzer.listening import Listening
from services.jb_analyzer.memory import MIN_COVERAGE, song_key
from services.jb_analyzer.sync import Sync

END_SILENCE_S = 20.0         # a song quiet this long is over
SURE = 0.5                   # the sync this sure: the map's sections take over from the live ones
VOICED = 0.4                 # the ears' voice (0..1 within the song) this high: someone's singing
MAP_FIELDS = ("start", "end", "bars", "label", "role", "n", "voice", "drums", "ring", "last", "peak")


class Songs:
    def __init__(self, rate: int, hop: int, ears=None):
        self.listening = Listening(rate, hop, ears)
        self.identity = {}
        self.key = None
        self.playing_from = None
        self.map = None
        self.sync = None
        self.section = None          # the map section the cabinet is in (its index)
        self.last_sound = None
        self.started = None
        self.ears_sum, self.ears_n, self.voiced_n = {}, 0, 0
        self.tags, self.tag_genre = [], None

    # Who ------------------------------------------------------------------------

    def now_playing(self, t: float, topic: str, msg: dict) -> dict:
        # a source that never says (some phones don't): playing, if it names a song
        playing = msg.get("status") in (None, "playing")
        if playing and msg.get("title"):
            self.playing_from = topic
        if topic != self.playing_from:
            return {"changed": False, "finished": None}
        dur = msg.get("duration_ms")
        new = {"title": msg.get("title"), "artist": msg.get("artist"), "album": msg.get("album"),
               "duration": dur / 1000 if isinstance(dur, (int, float)) and dur > 0 else None,
               "genre": msg.get("genre"), "year": msg.get("year"), "source": topic.split("/")[1]}
        out = {"changed": False, "finished": None}
        if playing and new["title"] and (new["title"], new["artist"]) != (self.identity.get("title"),
                                                                         self.identity.get("artist")):
            out = {"changed": True, "finished": self.finish(t)}
            self._new_song(t, new)
        elif new["title"] and new["title"] == self.identity.get("title"):
            for k in ("duration", "genre", "year", "album"):           # filled in a moment later
                if new[k] and not self.identity.get(k):
                    self.identity[k] = new[k]
            if new["duration"] and not self.key:
                self.key = song_key(new["artist"], new["title"], new["duration"])
            self._identity_to_recording()
        pos = msg.get("position_ms")
        if playing and isinstance(pos, (int, float)) and self.key:
            self.listening.position(t, pos / 1000)
            if self.sync:
                self.sync.source(t, pos / 1000)
        return out

    def _new_song(self, t, ident):
        self.identity = ident
        self.key = song_key(ident["artist"], ident["title"], ident["duration"])
        self.listening.new_song(dict(ident), t)
        self.map = self.sync = self.section = None
        self.started, self.last_sound = t, t
        self.ears_sum, self.ears_n, self.voiced_n = {}, 0, 0
        self.tags, self.tag_genre = [], None

    def _identity_to_recording(self):
        rec = self.listening.recording
        if rec is not None:
            rec.identity.update({k: v for k, v in self.identity.items() if v})
            rec.identity.update(tags=list(self.tags), tag_genre=self.tag_genre)

    def tagged(self, tags, genre):
        self.tags, self.tag_genre = list(tags or []), genre
        self._identity_to_recording()

    def learned(self, key, song_map: dict | None, rec: dict | None):
        """The map (and its recording, for the sync) of the song now playing."""
        if key != self.key or song_map is None:
            return
        self.map = song_map
        self.sync = Sync(rec) if rec is not None else None
        if self.sync and self.started is not None:
            self.sync.assume(self.started, 0.0)             # heard starting: probably from its start
            for t, p in self.listening.recording.anchors if self.listening.recording else []:
                self.sync.source(t, p)

    # The music --------------------------------------------------------------------

    def frame(self, t: float, stereo) -> tuple:
        li = self.listening
        public, events = li.frame(stereo, t)
        if li.pipe.levels.sounding:
            self.last_sound = t
        sy = self.sync
        if sy is not None:
            sy.frame(t, li.pipe.f)
            if li.pipe.tex is not None:
                sy.texture(t, li.pipe.tex)
        pos = self.position(t)
        # a known song follows its map: locked to the sound, or (no recording to sync to) by the source's word
        if self.map is not None and pos is not None and (sy is None or (sy.offset is not None and sy.sure >= SURE)):
            events = [e for e in events if e["type"] != "section"] + self._map_events(t, pos)
        else:
            for e in events:
                if e["type"] == "section" and e.get("source") == "live":
                    # live sections start on the clock: in the song, where the source puts it
                    e["at"] = e["start"]
                    e["start"] = self._in_song(e["at"])
        if public is not None and pos is not None:
            public["pos"] = round(pos, 3)
            public["synced"] = bool(sy is not None and sy.offset is not None)
        return public, events

    def position(self, t: float) -> float | None:
        """The song position now: locked to the map, else the source's word."""
        if self.sync is not None and self.sync.offset is not None:
            return self.sync.position(t)
        rec = self.listening.recording
        if rec is not None and rec.anchors:
            at, p = rec.anchors[-1]
            return p + (t - at)
        return None

    def _in_song(self, clock_t) -> float | None:
        """A moment on the clock as a song position (where the source, or the sync, puts it)."""
        pos = self.position(clock_t)
        return None if pos is None else round(pos, 3)

    def _map_events(self, t, pos) -> list:
        secs = self.map.get("sections") or []
        if pos is None or not secs:
            return []
        k = max(0, int(np.searchsorted([s["start"] for s in secs], pos, side="right")) - 1)
        if k == self.section:
            return []
        prev = secs[self.section] if self.section is not None and self.section < len(secs) else None
        self.section = k
        s = secs[k]
        size = float(s.get("energy", 0.5))
        before = float(prev.get("energy", size)) if prev else size
        return [{"type": "section", **{f: s[f] for f in MAP_FIELDS if f in s}, "size": size, "source": "map",
                 "at": round(t - (pos - s["start"]), 3), "change": 1.0,
                 "energy": "up" if size > before + 0.15 else "down" if size < before - 0.15 else "same"}]

    def heard(self, t: float, scores: dict) -> dict:
        groups = self.listening.heard(t, scores)
        flat = {**{k: v for k, v in groups.items() if isinstance(v, (int, float))}, **groups.get("moods", {})}
        for k, v in flat.items():
            self.ears_sum[k] = self.ears_sum.get(k, 0.0) + float(v)
        self.ears_n += 1
        self.voiced_n += 1 if self.listening.sections.voice_now >= VOICED else 0
        return groups

    # What it is ---------------------------------------------------------------------

    def feel(self) -> dict:
        if self.map is not None and self.map.get("feel"):
            return self.map["feel"]
        li = self.listening
        n = max(1, self.ears_n)
        means = {k: v / n for k, v in self.ears_sum.items()}
        drums = li.sections.windows
        drums = float(np.mean([w.drums for w in drums[-300:]])) if drums else None
        state = li.pipe.slow.state(True, estimate=False)
        return feel_of(self.tags, self.identity.get("genre") or self.tag_genre, self.identity.get("year"),
                       {"tempo": state.get("tempo"), "drums": drums, "brightness": state.get("brightness")},
                       {"moods": means} if self.ears_n else None, li.pipe.key.read(),
                       self.voiced_n / self.ears_n if self.ears_n >= 5 else None)

    def slow(self) -> dict:
        li = self.listening
        g = li.groups or {}
        return {"song": self.key, "known": self.map is not None, "key": li.pipe.key.read(),
                "ears": {k: g.get(k) for k in ("voice", "rap", "speech", "drums", "guitar", "synth", "keys")
                         if g.get(k) is not None} or None,
                "voice": round(li.sections.voice_now, 3), "feel": self.feel()}

    def map_payload(self) -> dict:
        if not self.key:
            return {"song": None}
        if self.map is not None:
            m = self.map
            return {"song": self.key, "known": True, "tempo": m.get("tempo"), "downbeats": m.get("downbeats"),
                    "sections": m.get("sections"), "feel": m.get("feel"), "key": m.get("key"),
                    "moments": [e for e in m.get("moments", []) if e.get("type") != "crash"],
                    "plays": m.get("plays"), "learned": m.get("learned")}
        live = [{**x, "at": x["start"], "start": self._in_song(x["start"])} for x in self.listening.sections.summary()]
        return {"song": self.key, "known": False, "sections": live, "feel": self.feel()}

    # Over -----------------------------------------------------------------------------

    def tick(self, t: float) -> dict | None:
        if self.key and self.last_sound is not None and t - self.last_sound > END_SILENCE_S:
            return self.finish(t)
        return None

    def finish(self, t: float) -> dict | None:
        """The song is over (another one started, or it went quiet). Its
        recording, if it heard the song through and the cabinet doesn't
        know it yet; then the cabinet starts over, with no song."""
        rec, key, known = self.listening.recording, self.key, self.map is not None
        self.key, self.map, self.sync, self.section, self.identity = None, None, None, None, {}
        self.last_sound = None
        if rec is None or key is None:
            return None
        self.listening.recording = None
        cover = rec.coverage()
        out = {"key": key, "arrays": None, "known": known, "coverage": round(cover, 3)}
        if cover >= MIN_COVERAGE and not known:
            out["arrays"] = rec.arrays()
        return out
