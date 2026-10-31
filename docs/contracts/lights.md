# Contract: the lights

Version 1 · **Draft, built for v0.2.0** · Oct 30, 2026

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

A show is a JSON file: a recipe from the engine's effect library, with no code, so uploading one is safe. `services/jb_lights/show/styles.py` checks it, and [Light Show Styles.md](../Light%20Show%20Styles.md) describes the effects.

```json
{
  "format": 1, "id": "my-show", "name": "My show", "version": "1.0.0", "author": "…",
  "for": ["rock", "metal"],
  "palettes": [["#ff2000", "#ffd000"]],
  "peak": "#ffffff", "light": 0.7, "strobe": 0.4, "gap": 0.5, "pace": 0.6, "drive": 0.7,
  "release": 0.4, "beat_locked": true,
  "roles": {
    "storyteller": {"calm": ["glow"], "main": ["ripple"], "peak": ["strobe"]},
    "rhythm": {"…": "…"}, "meter": {"…": "…"}, "keys": "show"
  },
  "inputs": {"palette": 0.3, "cues": {"lightning": "flicker", "doorbell": "ignore"}}
}
```

- **`for`** names genre groups. The show joins those groups' rotations.
- **`inputs.palette`** (0..1) says how strongly a module's palette signal tints this show. 0 means it ignores signals.
- **`inputs.cues`** maps cue names to effects: `flash`, `flicker`, `pulse`, `sparkle`, `sweep`, `ignore`. A cue the show doesn't name plays as a `pulse`.

Shows come from three places:

| Source | Location |
| --- | --- |
| Built-in | `services/jb_lights/shows/` |
| Uploaded | admin › Light Show, kept in `/var/lib/juk3-box/lights/shows/` |
| A module's | its manifest's `"shows": ["shows/x.json"]` |

A module's show ids start with the module's name: jb-weather can bring `weather` and `weather-storm`. When two shows share an id, the built-in one wins, then the module's, then the uploaded one.

The owner's choices are in `/var/lib/juk3-box/lights/settings.json` (`services/jb_lights/owner.py`): rotations per genre group, a pinned show, disabled shows, the idle look, brightness, low-flash, quiet hours, and each module's input on/off and strength.

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
