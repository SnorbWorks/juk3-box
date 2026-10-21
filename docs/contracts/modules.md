# Contract: modules, the registry and their lifecycle

Version 1 · **Draft, proven against the reference build** · Oct 13, 2026

The core knows no specific module. It knows that modules exist, what role each says it plays, and whether each is up. That is all the registry holds.

## A module declares itself: `module.json`

Every module ships a manifest beside its code (`services/<package>/module.json`). It is data only; nothing in it runs.

```json
{
  "contract": 1,
  "id": "jb-audio",
  "name": "Audio output",
  "description": "Owns the volume of the sound output.",
  "version": "1.0.0",
  "roles": ["output"],
  "publishes": ["jukebox/jb-audio/state"],
  "subscribes": ["jukebox/jb-audio/cmd"],
  "commands": ["volume_delta", "mute", "unmute", "toggle_mute"],
  "settings": "jb-audio",
  "essential": true
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `contract` | yes | This contract's version (1) |
| `id` | yes | Lowercase, hyphenated. Its bus name and systemd unit |
| `name`, `description` | yes / no | For people (the admin's Modules page) |
| `version` | yes | The module's own version |
| `roles` | yes | One or more of `source`, `analyzer`, `input`, `output`, `service` (see Roles) |
| `publishes`, `subscribes` | no | Topics, for the admin and for people reading it. Documentation for built-in modules; **enforced** for installed ones ([installed-modules.md](installed-modules.md)) |
| `commands` | no | Actions its `cmd` topic accepts |
| `settings` | no | The name of its settings file in `/etc/juk3-box/<name>.json`, if it has one |
| `essential` | no | `true` if the jukebox can't work without it; the admin warns before restarting it |
| `boards` | no | Per-board suggestions: `{"pi4": {"suggested": false, "why": "…", "cost": "about 30% of a core and 23 MB"}}`, by `shared/hardware.py`'s family names. Never a block: the admin labels it and asks before turning it on; the setup wizard starts that board with it off. Costs are measured on that board with music playing. Features inside a module (a setting) are in `hardware.FEATURES` |

## Discovery: the registry

The registry is not a running service. It is a view any module can build from two things:

1. **Present**: every `module.json` found under the installed modules folder (`/opt/juk3-box/services/*/module.json` for the reference build; a later plugin folder, `/var/lib/juk3-box/modules/*/module.json`, for added modules).
2. **Up**: the module's heartbeat on the bus (see [bus.md](bus.md), Liveness).

So the core needs no list of modules, and a new module appears by being installed. The admin (jb-admin) builds this view for its Modules page; `services/jb_admin/registry.py` is the reference implementation.

A module found on the bus with no manifest (a heartbeat from an unknown id) is shown as "undeclared": it works, but the owner can't see what it is.

Installed modules (added by the owner from a zip, sandboxed, with more manifest fields: `run`, `permissions`, `resources`, `owner_settings`, `blocks`) are specified in [installed-modules.md](installed-modules.md).

## Lifecycle

| Stage | What the module does |
| --- | --- |
| Install | Its folder and `module.json` appear; a systemd unit (`jb-<id>.service`) runs it |
| Start | Reads its config (`/etc/juk3-box/<settings>.json`, else defaults); connects to the bus; publishes its retained state |
| Run | Heartbeat every 2 s; publishes state on change; acts on its `cmd` |
| Config change | Re-reads config when `jukebox/jb-admin/changed` names it, or on restart (whichever it supports; restart always works) |
| Stop | Clears or updates retained state that would be stale (e.g. `connected: false`), then exits |
| Fail | Exits; systemd restarts it after 2 s. Other modules carry on: every module must survive any other module being absent |

## Roles

Roles describe what a module does in the pipeline, never what hardware it uses. All are optional. Each role's minimum contract is held open where only one case exists, as the platform plan requires.

| Role | Minimum contract (v1) | Reference module | Held open until |
| --- | --- | --- | --- |
| `source` | Publishes its songs (each with an `item_path` it can play), a `now-playing` state in the phone's shape, `play-error`, and accepts `play_item`, `play`, `pause`, `stop` on its `cmd` | `jb-source-phone` + `jb-library`; `jb-source-local` (USB drives); `jb-source-helper` (the juk3box Helper app, Oct 20, 2026) | Decided with the second source (Oct 14, 2026): each source keeps its own topics; jb-library merges the songs, jb-core sends each pick to its source by `item_path` (`drive:` = jb-source-local) and plays one source at a time, and subscribers show whichever source last reported `playing` |
| `analyzer` | Publishes under a role namespace (`analysis/<kind>/...`) with `v` | `jb-analyzer` | A second analyzer (e.g. video) |
| `input` | Publishes events describing what a human did (`gesture`, `key`), not what should happen | `jb-input` | A non-keypad input (touchscreen, phone remote) |
| `output` | Consumes state; publishes only its own state | `jb-ui` (kiosk), `jb-lights`, `jb-audio` | A second screen or a video output |
| `service` | Anything else: orchestration (`jb-core`), management (`jb-admin`), background work (`jb-crawl`) | | |

## Proving notes

- Every reference module fits a role. `jb-core` is orchestration (`service`) rather than a fifth role, which is the right call: the jukebox's rules (picks, queue, shuffle) are behavior a different product would replace wholesale.
- `jb-lights` doesn't heartbeat yet (it uses paho, not `shared/bus.py`); the registry shows it via systemd as a fallback. Fix when next touched.
- Settings live in two places today: per-module files in `/etc/juk3-box/` (root-owned, edited over SSH) and the owner's choices in `/var/lib/juk3-box/settings.json`. See [configuration.md](configuration.md).
