# Contract: audio analysis stream

Version 1 · Oct 12, 2026 · first contract written to the Platform plan
(`docs/Platform — Development Plan.md`)

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
| `jukebox/analysis/audio/events` | events, not retained | when they happen | Discrete moments: silence, clipping, a drop |
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
 "build": "rising",
 "phase": 0.04, "bar_beat": 0}
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

### `slow`

```json
{"v": 1, "t": 1234.5,
 "playing": true,
 "tempo": 124.0, "energy": 0.71, "brightness": 0.58, "busyness": 0.43,
 "mood": {"arousal": 0.74, "warmth": 0.39, "label": "energetic"},
 "identity": {"title": "...", "artist": "...", "album": "..."},
 "tags": ["synthwave", "80s", "electronic"], "genre": "synthwave"}
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
| `section` | The song moved on: the last few seconds sound clearly different from the stretch before (verse to chorus, the bridge). Carries `change` (0 .. 1, how different) and `energy` (`up`, `down`, `same`). At most every 10 s |

### `status`

```json
{"v": 1, "online": true, "module": "jb-analyzer", "rate": 48000, "fps": 30}
```

Retained; `online: false` when the analyzer stops cleanly. A consumer that
sees no analyzer (no status, or offline) should fall back to its own idle
look rather than freeze.

## Not in version 1 (deliberately)

- Song fingerprinting, mood tags from the web, the per-song cache and
  pre-analysis: later, as refinements; nothing here depends on them.
- Loudness normalisation acts on the audio, not the stream; it isn't part of
  this contract.
