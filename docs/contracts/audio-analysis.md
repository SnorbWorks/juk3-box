# Contract: audio analysis stream

Version 1 · Oct 12, 2026 · first contract written to the Platform plan
(`docs/Platform — Development Plan.md`) · Oct 30, 2026: the song itself
(the cymbals, where in the song it is, what kind of song, its sections by
name, its map) -- optional fields and one new topic, so still version 1

An **analyzer** module listens to what is playing and publishes a running
description of it. **Consumers** (lights, visuals, screens, logging) subscribe
and decide for themselves what to do with it. The topics are named for the
**role**, not for the module that fills them, so any analyzer can publish them
and no consumer depends on which one does. The reference analyzer is
`jb-analyzer` (`services/jb_analyzer/`); see `docs/Audio Analyzer — Architecture.md`
for how it works.

This is version 1, proven against one analyzer and one kind of content
(music from a phone). It stays open to change until a second analyzer or a
second content type tests it (Platform plan, "do not freeze a contract
against a single case").

## Rules every message follows

- JSON over the shared MQTT bus.
- Every payload carries `"v": 1` (the contract version). A consumer ignores
  messages with a `v` it doesn't understand; additions within a version are
  new optional fields only, so consumers must ignore fields they don't know.
- Values are normalised to `0.0 .. 1.0` unless a unit is named (`bpm`, `db`).
- `t` is the publisher's clock in seconds (monotonic). Use it for ordering
  and gaps, not wall time.

## Topics

| Topic | Kind | Rate | What |
| --- | --- | --- | --- |
| `jukebox/analysis/audio/fast` | stream, not retained | ~30 per second while sound plays; none in silence | The sound right now |
| `jukebox/analysis/audio/slow` | state, retained | every few seconds, and on change | What kind of music this is |
| `jukebox/analysis/audio/events` | events, not retained | when they happen | Discrete moments: silence, clipping, a drop, a crash, a new section |
| `jukebox/analysis/audio/map` | state, retained | on a new song, and as it learns | The song's map: its sections (and, for a song heard before, everything ahead) |
| `jukebox/analysis/audio/status` | state, retained | on start, stop and change | Whether an analyzer is running, and what it hears |

The fast channel says **when** things move; the slow channel says **what
kind** of movement fits. Consumers should let the slow channel frame the fast
one (e.g. the mood picks the palette, the beat picks the moment).

### `fast`

```json
{"v": 1, "t": 1234.567,
 "level": 0.62, "peak": 0.81,
 "bands": [0.71, 0.66, ...],
 "brightness": 0.44,
 "beat": true, "onset": 0.37,
 "kick": 0.9, "snare": 0.0, "hat": 0.4,
 "crash": 0.0, "ring": 0.12,
 "build": "rising",
 "phase": 0.04, "bar_beat": 0,
 "pos": 61.204, "synced": true}
```

| Field | Meaning |
| --- | --- |
| `level` | Overall loudness, smoothed, perceptually weighted, relative to the song's own loud parts over the last minute or so (0 silent, 1 as loud as this song gets). Ignores the source's volume, so it always uses the full range |
| `peak` | Highest sample this frame, 0..1 of full scale (1 = clipping) |
| `bands` | 32 frequency bands, low to high, log-spaced from ~40 Hz to ~16 kHz, perceptually weighted and smoothed so they rise fast and fall gracefully. Consumers with fewer or more columns resample |
| `brightness` | Where the energy sits: 0 dark and bassy, 1 bright and airy |
| `beat` | `true` on the frame a beat (kick) lands |
| `onset` | How strong the newest attack was (0 none) |
| `build` | Trajectory over the last several seconds: `none`, `rising` (a riser), `breakdown` (thinned out, a drop may follow), `drop` (bass slammed back). Expect the occasional head-fake |
| `kick`, `snare`, `hat` | Each part of the drum kit: how far its newest attack stands out from its own last second and a half, 0 (nothing new) .. 1 (a clear hit). Kick ~40-150 Hz, snare's crack ~1.5-5 kHz, hats and cymbals ~7-16 kHz. Lets consumers give each drum its own job |
| `phase` | The beat clock: where we are within the beat, 0 on the beat .. 1 just before the next, or `null` until the tempo is known. Runs at the tempo and is pulled into step by the detected kicks, so it keeps time through gaps and fills where no kick lands |
| `bar_beat` | Which beat of a 4-beat bar (0 = the downbeat), or `null` with `phase`. Re-anchored to 0 on a hit, a new section or a drop (they almost always land on the one) |
| `crash` | 0, or 0.2 .. 1 on the frame a crash cymbal is called: a big jump in the cymbals' sizzle (7-16 kHz, broadband with it) that then *rang on* -- a hat, a clap or a snare jumps as far but is gone in a tenth of a second. So it's called about 0.17 s after it struck, and carries `crash_ago` (seconds since it struck) to place it. Also sent as a `crash` event. Added in v1 (Oct 30) |
| `crash_ago` | With a `crash` only: how long ago it struck, in seconds |
| `ring` | How loud the cymbals' wash is between hits, 0 .. 1 (the sizzle's floor over the song's own): a ride or a crash ringing lifts it, a busy hat pattern doesn't. Added in v1 (Oct 30) |
| `pos` | Where in the song it is now, in seconds, when known: locked to the sound for a song the cabinet has learned (`synced`), else the source's own word (which runs a Bluetooth delay or so off). Absent with no song |
| `synced` | `true` when `pos` is locked to the sound (a learned song, its recording matched to what's playing), so its map's times can be trusted to a frame or two |

### `slow`

```json
{"v": 1, "t": 1234.5,
 "playing": true,
 "tempo": 124.0, "energy": 0.71, "brightness": 0.58, "busyness": 0.43,
 "mood": {"arousal": 0.74, "warmth": 0.39, "label": "energetic"},
 "identity": {"title": "...", "artist": "...", "album": "..."},
 "tags": ["synthwave", "80s", "electronic"], "genre": "synthwave",
 "song": "3f9a1c0d2b7e4a65", "known": true,
 "key": {"key": "A", "mode": "minor", "major": -0.6, "sure": 0.7},
 "ears": {"voice": 0.08, "drums": 0.21, "synth": 0.12, "guitar": 0.0},
 "voice": 0.82,
 "feel": {"energy": 0.72, "valence": 0.41, "tender": 0.12, "aggressive": 0.08,
          "groove": 0.63, "moody": 0.55, "pace": 0.58, "voice": 0.7,
          "era": "80s", "words": ["moody", "groovy", "mid-tempo", "vocals", "80s"]}}
```

| Field | Meaning |
| --- | --- |
| `playing` | Sound is present (false after the silence event) |
| `tempo` | Beats per minute, or `null` until it's confident |
| `energy` | Sustained loudness and density over the last ~10 s |
| `brightness` | Sustained brightness over the last ~10 s |
| `busyness` | How often attacks happen (sparse 0 .. busy 1) |
| `mood.arousal` | Calm 0 .. energetic 1 (from tempo, energy, busyness; nudged a little by `tags` like "chill" or "party") |
| `mood.warmth` | Bright/sharp 0 .. warm/dark 1 (from brightness) |
| `mood.label` | One of `calm`, `moody`, `bright`, `energetic` -- a convenience; prefer the two axes |
| `identity` | What is playing, when known (from the source's own metadata today; fingerprinting later). Fields may be `null` |
| `tags` | What listeners call this song (Last.fm top tags, lowercase, up to 6), `[]` until known or when offline. Added in v1 without a version bump: consumers ignoring unknown fields are unaffected |
| `genre` | The first well-known genre among `tags`, or `null` |
| `song` | The song's id (by artist, title and length), or `null` with no song. The `map` topic carries the same id |
| `known` | `true` when the cabinet has learned this song (heard it through once before): its `map` is the whole song, ahead of time |
| `key` | The song's key so far (`key`, `mode` major/minor, `major` -1 minor .. 1 major as it leans, `sure` 0..1), or `null` |
| `ears` | What the sound-recognition model (YAMNet, about once a second) hears now, by group: `voice`, `rap`, `speech`, `drums`, `guitar`, `synth`, `keys` -- small numbers in a full mix, so compare them with each other, not with 1. Absent when the cabinet runs without the model |
| `voice` | How sung this moment is within the song, 0 (no voice) .. 1 (as sung as it gets) |
| `feel` | What kind of song it is, the way a lighting designer would sum it up, all 0..1: `energy`, `valence` (sad 0 .. happy 1), `tender` (a love song, a ballad), `aggressive`, `groove` (dance), `moody`, `pace` (how the lights should move: slow 0 .. fast 1 -- the tempo, held back for a tender or moody song), `voice` (how much of it is sung), `era` (a decade like `"80s"`, or `null`) and `words` (a few plain words for people). Settles over the first seconds as the tags, the model and the audio come in; a learned song's is worked out from the whole of it |

`song` .. `feel` added in v1 (Oct 30).

Mood follows the moment: it is a moving average over seconds, not one value
per song, so a quiet bridge pulls it down and the chorus brings it back.

### `events`

```json
{"v": 1, "t": 1234.5, "type": "silence"}
```

Some events carry more fields (below), e.g. `{"v": 1, "t": 1234.5, "type": "hit", "strength": 0.8, "after_gap": true}`.
Added in v1 without a version bump, like `tags`: consumers ignore types and fields they don't know.

| `type` | When |
| --- | --- |
| `silence` | The level has been near zero for about 1.5 s (song ended, paused, gap) |
| `sound` | Sound is back after a silence |
| `clip` | The signal is pinning near full scale (distortion; protect the amp) |
| `drop` | A build resolved into a drop (the same moment `build` becomes `drop`) |
| `gap` | Mid-song, the sound suddenly fell away (~14 dB under the song for ~0.1 s): the beat of silence before a drop, a band stopping dead. Not sent in a song's first seconds, nor as it fades out |
| `hit` | A big attack out of something quieter: the sound slamming back after a gap, a crash, the drop landing. Carries `strength` (0.3 .. 1) and `after_gap` (true when it ends a gap) |
| `section` | A new section of the song (below). Carries `change` (0 .. 1, how different) and `energy` (`up`, `down`, `same`) as it always has |
| `crash` | A crash cymbal (the fast channel's `crash`): `strength` 0.2 .. 1 and `ago`, how long ago it struck (it's called once it has rung on, ~0.17 s later). Added in v1 (Oct 30) |

#### Sections

A `section` event says the song is now in a new part of itself, and which:

```json
{"v": 1, "t": 1234.5, "type": "section", "source": "map",
 "label": "B", "n": 2, "role": "chorus", "size": 0.91, "voice": 0.84, "drums": 0.7,
 "start": 61.2, "at": 1234.48, "change": 1.0, "energy": "up",
 "end": 76.4, "bars": 8, "ring": 0.3, "last": true, "peak": true}
```

| Field | Meaning |
| --- | --- |
| `source` | `live` -- found as it happens, the first time a song plays: a bar and a beat after it starts, judged on the song's own phrase grid; `map` -- a learned song, from its map, on time (the moment `pos` crosses its start) |
| `label` | Which music: sections with the same letter are the same music coming back (the chorus again). Live labels can be corrected a bar later (an `update`) |
| `n` | Which time round this label is (1 the first) |
| `role` | What the section is: `intro`, `verse`, `pre-chorus`, `chorus`, `bridge`, `solo`, `breakdown`, `build`, `drop`, `outro`, `interlude`. Live roles are guesses (the loudest label that comes back is the chorus); a map's come from the whole song |
| `size` | How big it is within the song, 0 .. 1 (live: within the song so far) |
| `voice`, `drums` | How sung it is, how much the drums drive it, 0 .. 1 |
| `start` | Where it starts in the song, in seconds (as `pos` counts), or `null` with no position |
| `at` | When it started on the publisher's clock (`t`'s), so a consumer can tell how late it heard |
| `update` | `true`: a correction to the section just announced (same `start`), not a new one |
| `end`, `bars`, `ring`, `last`, `peak` | Map sections only: where it ends, how many bars, how much the cymbals ring through it, `last` on the song's last chorus (its send-off) and `peak` on its biggest section |

`label` .. `update` added in v1 (Oct 30); a consumer that only knew
`change` and `energy` still works.

### `map`

The song's map, retained, so a consumer can see what's ahead. A song the
cabinet hasn't learned has the sections heard so far; a learned one (the
analyzer studies a song after it has heard it through once, then follows its
map on every later play) has all of it:

```json
{"v": 1, "t": 1234.5, "song": "3f9a1c0d2b7e4a65", "known": true,
 "tempo": 119.8, "downbeats": [0.42, 2.43, ...],
 "sections": [{"start": 0.0, "end": 15.1, "bars": 8, "label": "A", "n": 1, "role": "intro",
               "energy": 0.21, "voice": 0.1, "drums": 0.3, "ring": 0.0}, ...],
 "moments": [{"t": 44.97, "type": "drop"}, {"t": 60.1, "type": "gap"}, ...],
 "feel": {...}, "key": {...}, "plays": 3, "learned": "2026-10-30T21:04:11"}
```

| Field | Meaning |
| --- | --- |
| `song` | The song's id (the slow channel's `song`), or `null` with no song (then nothing else) |
| `known` | A learned map (`true`) or the live one so far (`false`: `sections` and `feel` only, each section's `at` on the clock) |
| `tempo`, `downbeats` | The song's tempo and where every bar starts, in song seconds |
| `sections` | Every section, in order (fields as the `section` event's; `energy` is the map's `size`) |
| `moments` | The gaps, hits, drops and builds, at song seconds |
| `feel`, `key` | As on the slow channel, from the whole song |
| `plays`, `learned` | How many whole plays it has heard; when it learned it |

Added in v1 (Oct 30).

### `status`

```json
{"v": 1, "online": true, "module": "jb-analyzer", "rate": 48000, "fps": 30, "ears": true}
```

`ears`: whether it runs the sound-recognition model (added in v1, Oct 30).

Retained; `online: false` when the analyzer stops cleanly. A consumer that
sees no analyzer (no status, or offline) should fall back to its own idle
look rather than freeze.

## Not in version 1 (deliberately)

- Song fingerprinting: identity comes from the sources. (Mood tags from the
  web, and the per-song memory -- the `map` -- came in v1 on Oct 12 and
  Oct 30.)
- Loudness normalisation acts on the audio, not the stream; it isn't part of
  this contract.
