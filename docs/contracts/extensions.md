# Contract: extensions (installed modules, blocks, settings, permissions)

Version 1 · **Draft, proven against one module (Weather)** · Oct 13, 2026

How a module someone else wrote joins the platform: what it declares, what it may contribute to the screen and the admin, and the fence it runs inside. Builds on [modules.md](modules.md) (the manifest, registry and lifecycle) and [bus.md](bus.md). The direction behind it is in the [platform plan](../Platform%20—%20Development%20Plan.md), "Direction: a platform of many modules".

Two kinds of module:

- **Built-in**: ships with the platform (`/opt/juk3-box/services/<package>/`), trusted, runs as the system user. The jukebox's own modules.
- **Installed**: added by the owner from a zip (`/var/lib/juk3-box/modules/<id>/`), untrusted, sandboxed, and given only what it declares and the owner approved.

Everything below applies to installed modules; built-in modules may use the same manifest fields (owner settings, blocks) and get the same admin features.

## The manifest, extended

All of [modules.md](modules.md)'s fields, plus:

```json
{
  "contract": 1,
  "id": "jb-weather",
  "name": "Weather",
  "description": "Current weather for a place you choose.",
  "version": "1.0.0",
  "roles": ["source"],
  "run": "main.py",
  "publishes": ["jukebox/jb-weather/current"],
  "subscribes": ["jukebox/jb-weather/cmd"],
  "permissions": { "network": "internet" },
  "resources": { "cpu": 10, "memory_mb": 96 },
  "owner_settings": {
    "place": { "type": "text", "label": "Place", "default": "New York" },
    "units": { "type": "enum", "label": "Units", "default": "fahrenheit",
               "options": [{ "value": "fahrenheit", "label": "°F" }, { "value": "celsius", "label": "°C" }] }
  },
  "blocks": [{
    "id": "weather", "label": "Weather", "description": "The weather where you are.",
    "group": "content", "icon": "cloud-sun",
    "topic": "jukebox/jb-weather/current",
    "template": "blocks/weather.html", "style": "blocks/weather.css",
    "icons": ["sun", "cloud", "cloud-sun", "cloud-rain"],
    "shapes": [["compact", 0], ["card", 0.42], ["banner", 0.95]]
  }]
}
```

| Field | For | Meaning |
| --- | --- | --- |
| `run` | installed | The Python file to run, inside the module's folder. It runs with the platform's Python and may import the platform SDK (`shared.bus`, `shared.module_sdk`) and the standard library; other Python packages aren't installable yet |
| `permissions.network` | installed | `none` (default): only the bus. `internet`: the internet, but not this Pi's own services or the local network. (`lan` is reserved: it would let a module reach the open bus listeners, so it isn't granted until the core moves to bus logins) |
| `permissions.devices` | installed | Reserved (`bluetooth`, `audio`, `gpio`...). None are granted in version 1 |
| `resources` | installed | `cpu`: percent of one core (default 25, at most 100). `memory_mb` (default 128, at most 512). Enforced; shown on the module's card |
| `owner_settings` | both | Settings the owner changes in the admin (the module's card, Settings). Types: `text`, `number` (`min`, `max`), `enum` (`options`), `toggle`, `secret` (stored privately, never shown back in full), `location` (the owner types a ZIP code or a town and picks the exact match; the module gets `{name, admin1, country, latitude, longitude, timezone, label}`) |
| `blocks` | both | Blocks the module adds to the page builder (see Blocks) |

`publishes` and `subscribes` are **enforced** for installed modules (see Bus access); for built-in ones they remain documentation.

## Blocks

A module's block is **declarative**: markup and style files plus data off the bus. No code from a module ever runs in the cabinet's page.

- **Name.** A block's `id` is its name in layouts. It must be the module's id without `jb-` (`weather`) or start with it and a hyphen (`weather-week`), so modules can't collide with each other or with the engine's blocks.
- **Data.** The engine subscribes to `topic` and binds the latest message's fields into the template, as theme block markup does ([theme-format.md](../theme-format.md), Block markup): `data-text`, `data-src`, `data-if`, `data-if-not`, `data-icon` (an icon name the payload gives, from the block's `icons`, drawn from the bundled Lucide set). Make the topic retained so a new screen shows data at once.
- **Markup** is sanitized like a theme's (no scripts, no event handlers, no outside URLs). **Style** is served inside `@scope (jb-<name>)`, so it can't reach anything else on the screen (a style sheet whose braces don't balance, or that uses `@import`, is refused). It's loaded before the theme's styles, so a theme can restyle a module's block (`jb-<name> ...`).
- **Shapes.** `shapes` works as for the engine's own blocks (`data-shape`, a Layout setting in the page builder).
- **Settings.** `settings` gives the block its own settings in the page builder: `{"show-weather": {"type": "bool", "label": "Show the weather", "default": true}}` (types `bool`, `enum`, `text`, `int`, as the engine's block schema). The engine sets each on the block's element as an attribute (`show-weather="false"`), for the block's style to use.
- Until data arrives the block shows nothing (`data-empty` on its element).
- **Not yet:** cabinet screens from modules; knob stops in display blocks (playlist blocks have theirs).

## Bus access (installed modules)

- Each installed module gets its own bus login, `jbmod-<id>`, and connects over a private socket, `/run/mosquitto/modules.sock`, where anonymous access is refused. The login arrives as a systemd credential; the SDK's `Bus` uses it automatically.
- What it may do: **publish** under its own namespace, `jukebox/<id>/#` (its heartbeat, state, everything); **subscribe** to the topics in its `subscribes`, its own `jukebox/<id>/#`, and `jukebox/jb-admin/changed`.
- Its sandbox denies it this Pi's own address and the local network, so it can't reach the open listeners (1883, 9001) instead. Built-in modules and the cabinet's screen still use those open listeners until the core is moved to logins (Phase 8).

## The sandbox

Each installed module runs as its own systemd unit, `jbmod-<id>.service`:

- a throwaway system user (`DynamicUser`) with no login, no home, and no access to other modules' files or the owner's data
- the whole filesystem read-only except its own state folder (`$STATE_DIRECTORY`, kept across restarts and updates)
- no devices; no new privileges; kernel settings, modules and logs protected
- network as its permission says; CPU and memory capped by `resources`
- secrets and its bus login handed over as systemd credentials (`$CREDENTIALS_DIRECTORY`), readable by it alone

The owner's settings for it are passed as `$JB_SETTINGS` (a file, non-secret values) and the `secrets` credential. Settings are read when the module wants (it re-reads after `jukebox/jb-admin/changed` names it); secrets are handed over when it starts, so the admin restarts an installed module when one of its secrets changes.

## Playlist blocks

A block with `"kind": "playlist"` is something the owner can play, not just look at. Its topic carries `{hook, subtitle, icon, songs: [{title, artist}], autoplay_after}`:

- The engine matches `songs` to the library, so only songs the jukebox can play count (`count`, `countLabel`, `cover1`-`cover4` are added for the template).
- Its template's `data-stop="open"` opens the list on the song board, titled by `hook`; `data-stop="play"` queues it all, shuffled.
- It can also be one of a Browse row's cards (its block name in Browse's shortcuts).
- `autoplay_after` (minutes, 0 = never) asks the cabinet to play it once the jukebox has been quiet that long: nothing playing or queued, nobody at the keypad, a phone connected; once per quiet spell.

Proven with **Weather Mix** (`modules/jb-weathermix/`): it also proves modules using each other's data (it subscribes to Weather's topic, with permission).

## Installing

A module is a zip holding one folder: `<id>/module.json`, its `run` file and anything else it needs (templates, styles). At most 200 files and 20 MB; no links; no paths outside the folder.

1. **Upload**: the admin unpacks it into a holding folder and checks the manifest: contract version, id (not a built-in's, not reserved), roles, `run` file present, topics in its own namespace for `publishes`, permissions and resources it can grant, block names and files.
2. **Review**: the admin shows the owner what it asks for (network, topics it listens to, CPU and memory, blocks it adds). Nothing runs before the owner says Install.
3. **Install**: its files go to `/var/lib/juk3-box/modules/<id>/`; a privileged helper (`jb-modctl`, the only part that runs as root) creates its bus login and topic rules and its unit, and starts it.
4. **Health check**: it must heartbeat within 60 seconds of starting and keep running. If not, it's turned off and removed, or on an update the previous version comes back. The owner sees why.
5. **Update**: uploading the same id again replaces it (the previous version kept for the rollback). **Uninstall** stops it and removes its unit, bus login, files and settings.

## Contract versions

`contract` is the manifest's contract version. The platform accepts the versions it knows (1) and refuses others with the reason, so an old platform never runs a module that expects more than it gives.

## Proving notes

- Proven with **Weather** (`modules/jb-weather/` in the repo; install it from the admin): internet access, owner settings, a block with shapes, heartbeat, the health check and uninstall.
- Held open for a second case: block settings, module screens, devices, per-module Python packages, `lan` network access, and how two modules share data beyond the bus.
