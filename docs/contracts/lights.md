# Contract: the lights

Version 1 · **Draft, built for v0.2.0; show format 2 in v0.3.0** · Oct 30, 2026

The cabinet's LEDs are a platform inside the platform, on the same split as the screen: **jb-lights owns the hardware, shows are data, modules talk over the bus.** It works like WordPress. Cues are its actions: something happened, and whatever is listening reacts. Signals are its filters: they adjust what's shown. Overlays are a plugin's own widget. The engine keeps the order and the rules, and nothing reaches an LED without passing its safety pass.

Four parts:

1. **Fixtures**: what's wired (a file).
2. **Shows**: what to play (files).
3. **Inputs**: what modules add (bus messages).
4. **Layers and safety**: how the engine puts it together, every frame.

## Fixtures

`services/jb_lights/fixtures/juk3box-cabinet.json` (read by `services/jb_lights/fixtures.py`) describes what's wired.

A **chain** is one data line:

| Field | Meaning |
| --- | --- |
| `id` | e.g. `accent` |
| `driver` | `pio` (Pi 5), `spi` or `pwm` |
| `gpio` | the data pin |
| `pixels` | how many pixels are on it |
| `order` | the colour order: `GRB`, `RGB`, `GRBW`, `RGBW` |
| `max_amps` | the software current cap, kept under the chain's fuse; never above 10 A |

A **fixture** is a run of a chain's pixels that is one thing in the cabinet. It has an `id`, a `role`, `start` and `count` (inside the chain, with no overlaps) and a `layout`.

**Roles** are what shows aim at. A show never names pixel numbers.

| Role | This cabinet |
| --- | --- |
| `storyteller` | the upper panel |
| `rhythm` | the footlights |
| `meter` | the level bar |
| `keys` | the keypad |

**Layouts** place every pixel. All measurements are in inches from the top-left of the upper panel's LEDs:

- **`map`**: camera-mapped points (`file`, holding `aspect` and `points: [[n, x, y], …]` with x and y 0..1 across the fixture's own extent; made with tools/ledmap), placed at `at_in` and `width_in` wide.
- **`grid`**: `rows` × `cols`, evenly spaced.
  - `first` is the corner where the chain starts, and `at_in` is that pixel's centre.
  - `pitch_in` is the spacing.
  - `order` (`rows` or `columns`) is the way the chain runs.
  - With `serpentine`, it turns back at the end of each line.
- **`keypad`**: the keypad; the keys know their own places.

The **picture** (`width_in`, `aspect`) is the shared space every show draws in. x runs 0..1 across the upper panel. y uses the same scale down the cabinet: one picture unit is `width_in / aspect` inches.

v0.2.0 reads only the built-in file. The owner's own file, made by the admin's LED wizard for new panels and strips, comes later. When it does, its caps can only be lower than the built-in ones.

## Shows

A show is a JSON file: a recipe from the engine's effect library, with no code, so uploading one is safe. `services/jb_lights/show/styles.py` checks it (`check()`, which the admin's upload uses too), and [Light Show Styles.md](../Light%20Show%20Styles.md) describes the effects and how a song's show is planned.

Format 2 (v0.3.0) adds what the choreographer plans each song with: what kind of song the show suits, how it moves, what its big moments do, and a look for each kind of section. Format 1 files still load.

```json
{
  "format": 2, "id": "my-show", "name": "My show", "version": "1.0.0", "author": "…",
  "description": "Beams on the big moments, the spectrum for the voice.",
  "for": ["rock", "metal"],
  "fits": {"energy": 0.85, "aggressive": 0.6, "pace": 0.6, "eras": ["80s"], "tags": ["arena rock"]},
  "palettes": [["#FF2A00", "#FFB000", "#FFFFFF"], ["#FFFFFF", "#2F6BFF", "#FF2A00"]],
  "peak": ["#FFFFFF", "#FFD27A", "#FF2A00"],
  "light": [0.25, 1.0], "strobe": 1, "drive": "kick", "release": 0.14, "beat_locked": true,
  "motion": {"pace": "auto", "smooth": "auto"},
  "accents": {"crash": "splash", "drop": "slam", "gap": "blackout"},
  "roles": {
    "storyteller": {"calm": ["wash"], "main": ["beams", "slab"], "peak": ["beams", "sweep"]},
    "rhythm": {"calm": ["glow"], "main": ["beams", "pulse"], "peak": ["beams", "rows"]},
    "meter": {"calm": ["vu"], "main": ["drumkit", "fire"], "peak": ["fire", "sparks"]},
    "keys": "pulse"
  },
  "sections": {
    "vocal": {"storyteller": ["rise", "eq"], "meter": ["spectrum", "vu"], "keys": "eq"},
    "big": {"storyteller": ["beams"], "rhythm": ["beams", "rows"], "meter": ["fire", "sparks"]}
  },
  "inputs": {"palette": 0.3, "cues": {"lightning": "flicker", "doorbell": "ignore"}}
}
```

| Field | Meaning |
| --- | --- |
| `for` | Genre groups (`edm`, `bass`, `hiphop`, `pop`, `rock`, `metal`, `oldies`, `country`, `soul`, `jazz`, `chill`, `latin`). The show is made for their songs: it gets the genre bonus for them, and takes turns in their rotation until a song's feel is in |
| `fits` | Format 2. What kind of song it suits: targets 0..1 on the analyzer's feel (`energy`, `valence`, `tender`, `aggressive`, `groove`, `moody`, `pace`, `voice`: `docs/contracts/audio-analysis.md`, `feel`), `eras` (decades, `50s` to `20s`) and `tags` (up to 20 listeners' tags). An axis it doesn't name is taken to be a typical song's |
| `palettes` | 1–8 palettes of 1–8 colours. A song keeps one of them all the way through: the one whose warmth suits its feel |
| `peak` | The colours for the song's peak, its last chorus and a drop |
| `light` | `[floor, ceiling]`, 0..1: how dark its quietest look gets, how bright its biggest |
| `strobe` | `0` never flashes white; `1` on big hits and drops; `2` also strobes the builds |
| `drive` | What the footlights follow: `kick`, `snare`, `offbeat` or `level` (a smooth song follows the level whatever this says) |
| `release` | 0.05–2 s: how long a pulse takes to fade; a smooth song stretches it |
| `beat_locked` | `false`: swing and rubato fool beat clocks, so follow the level |
| `motion` | Format 2. `pace`: `auto` (the song's own), or lean it `half`, `beat` or `double`; `smooth`: `auto` (the song's), or 0 (punchy) to 1 (flowing), met halfway by the song's |
| `accents` | Format 2. `crash`: `splash`, `flash`, `shimmer` or `none`; `drop` (a drop, or the hit after a gap): `slam`, `flash`, `swell` or `burst`; `gap`: `blackout`, `dim` or `""` (ignore it). A gentle song (tender, or very smooth) swells and shimmers whatever these say |
| `roles` | Scenes for each role (`storyteller`, `rhythm`, `meter`) in each tier (`calm`, `main`, `peak`), 1–12 each, taking turns; `keys` is one keypad scene |
| `sections` | Format 2. Scenes per section character (`quiet`, `vocal`, `drive`, `big`, `build`, `break`, `solo`), per role. A character or role it leaves out plays its tier's scenes (quiet and break: calm; vocal, drive and build: main; big and solo: peak) |
| `inputs.palette` | 0..1: how strongly a module's palette signal tints this show. 0 means it ignores signals |
| `inputs.cues` | Cue names to effects: `flash`, `flicker`, `pulse`, `sparkle`, `sweep`, `ignore`. A cue the show doesn't name plays as a `pulse` |

A format 1 file has no `fits`, `motion`, `accents` or `sections`. It keeps its top-level `pace` (`half`, `beat`, `double`) and `gap`, its tiers stand in for the characters, and it's taken to suit a typical song of its genre groups.

**How a song's show is chosen** (`services/jb_lights/show/library.py`, `choose`):

1. A **pinned** show is the look for every song, whatever the song.
2. Otherwise, once the analyzer has the song's feel, every enabled show made for songs is scored against it: its feel first, then its genre group, tags and decade. If the owner has limited the song's genre group to some shows, only those are scored. Shows within a whisker of the best take turns, song by song, so a song always gets the same one.
3. Until the feel is in, the song's genre group takes turns among its shows.

A show with neither `for` nor `fits` is an idle show (like Weather's): it plays when there's no music, never to a song.

Shows come from three places:

| Source | Location |
| --- | --- |
| Built-in | `services/jb_lights/shows/` |
| Uploaded | admin › Light Show, kept in `/var/lib/juk3-box/lights/shows/` |
| A module's | its manifest's `"shows": ["shows/x.json"]` |

A module's show ids start with the module's name: jb-weather can bring `weather` and `weather-storm`. When two shows share an id, the built-in one wins, then the module's, then the uploaded one.

The owner's choices are in `/var/lib/juk3-box/lights/settings.json` (`services/jb_lights/owner.py`): the shows each genre group is limited to, a pinned show, disabled shows, the idle look, brightness, low-flash, quiet hours, and each module's input on/off and strength.

## Inputs from modules

A module adds to the lights over its own topics. The broker gives each installed module readwrite on `jukebox/<id>/#` only, so the topic itself proves who sent a message.

| Topic | Kind | Payload |
| --- | --- | --- |
| `jukebox/<id>/lights/signal` | retained state | `{"v": 1, "palette": ["#hex", …], "intensity": 0..1}` |
| `jukebox/<id>/lights/cue` | event | `{"v": 1, "cue": "lightning", "strength": 0..1, "color": "#hex"}` |
| `jukebox/<id>/lights/overlay/<name>` | retained state | `{"v": 1, "on": "rhythm", "priority": 1..9, "ttl": 1..600, "look": {…}}` |

**Signals** are continuous.

- `palette` (1–8 colours) tints the show's colours, by the show's `inputs.palette`.
- `intensity` scales the base look. 1 means unchanged, and it can only dim.
- An empty payload clears the signal. If two modules send a palette, the most recent one wins.

**Cues** are named one-offs.

- A name is lowercase, with dashes (`lightning`, `doorbell`, `timer-done`).
- `strength` defaults to 1. `color` defaults to white, or the module's palette.
- The running show (or the idle look) decides what a cue becomes, using its `inputs.cues`.

**Overlays** let a module take part of the cabinet for a while.

- `on` is a role, or a fixture id.
- A higher `priority` wins over a lower one.
- `ttl` is required, at most 600 s. When it runs out the overlay is gone, unless the module sends it again.
- An empty payload removes the overlay.
- `look` is one of:
  - `{"kind": "fill", "color": "#hex", "level": 0..1}`
  - `{"kind": "bar", "color": "#hex", "level": 0..1}`: a bar filling left to right
  - `{"kind": "pulse", "color": "#hex", "speed": 0.2..4}`: breathing, in Hz
  - `{"kind": "sparkle", "color": "#hex", "level": 0..1}`

**The keypad** (`on: "keys"`) can be lit by an overlay, but never stops its own functions. While someone is choosing a song, after Reset, or with the power menu open, the keypad shows its own feedback as it always has. The overlay is back once that's done.

**Permission.** A module asks for lights in its manifest, the owner approves it at install (the review lists it), and jb-lights ignores anything the manifest didn't ask for:

```json
"lights": {"signal": true, "cues": ["lightning", "rain"], "overlays": ["keys", "rhythm"]}
```

The owner can turn each module's lights off or down in admin › Light Show › Inputs. Strength 0..1 scales its signal's tint, its cues and its overlays.

Bad payloads are dropped. A payload that is not valid JSON, has the wrong `v`, uses an unknown cue or role, or asks for a role or cue the manifest didn't, is ignored, and the last reason is shown in the Inputs section.

## Layers and safety

Every frame, for each chain, the engine draws these layers in order:

1. **base**: the idle look, or the music show.
2. **signals**: modules' palettes tint it, and their intensity dims it.
3. **cues**: short effects, each one at most 1.5 s.
4. **overlays**: by priority, on their fixtures.
5. **feedback**: the keypad's own (a pick, Reset, the power menu). It always wins.

Then comes the **safety pass**, which nothing gets past:

- the owner's brightness, and the quiet-hours brightness while that's on (whichever is lower)
- **low-flash**: a white flash becomes a soft swell, and drops burst gently
- **at most 3 flashes a second**: a jump of the whole cabinet to bright counts as one, and further ones are held back
- each chain's **current budget** (its `max_amps` and the global limiter), the same as before

The safety pass is the last step in every chain's loop: the accent chain, the level bar and the keypad.
