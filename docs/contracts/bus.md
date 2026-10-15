# Contract: the bus

Version 1 · **Draft, proven against the reference build** · Oct 13, 2026

The bus is the platform's hook system. Modules never call each other; they publish and subscribe. A module someone else writes joins by speaking this contract, without any change to the core or to another module.

The bus is MQTT (Mosquitto): port 1883 for services, port 9001 (WebSockets) for browser pages such as the kiosk engine and the admin preview.

## Naming

```
jukebox/<module>/<channel>
```

- `<module>` is the publishing module's id: lowercase, hyphenated, the same as its manifest `id` and its systemd unit (`jb-core`, `jb-source-phone`).
- `<channel>` names what's published: a noun for state (`queue`, `now-playing`, `state`), `cmd` for commands **to** that module, `heartbeat` for liveness.
- A module publishes only under its own name. Anyone may publish to another module's `cmd`.
- A shared stream that is a public interface in its own right, with more than one possible publisher, gets a role namespace instead: `jukebox/analysis/audio/...` (see [audio-analysis.md](audio-analysis.md)). Use this sparingly. It is for contracts, not for one module's convenience.

The `jukebox/` prefix is the reference build's namespace and stays as is: renaming it would break every module for no gain. A future build of a different product could use another prefix; nothing in a module may assume the prefix beyond reading it from config.

## Message shape

- Payloads are JSON objects (never bare values), UTF-8.
- An empty payload on a retained topic deletes it (MQTT's rule). Subscribers ignore empty payloads.
- A payload that is a public interface carries `"v": <int>`. Adding a field is not a breaking change; removing or changing the meaning of one is, and bumps `v`. Older payloads without `v` are version 1.
- Unknown fields must be ignored by subscribers, so publishers can add fields freely.

## Three kinds of channel

| Kind | Retained | Rate | Examples |
| --- | --- | --- | --- |
| **State**: the current truth | yes | on change | `jb-core/queue`, `jb-audio/state`, `jb-library/tracks` |
| **Event**: something happened | no | as it happens | `jb-input/gesture`, `jb-core/selection`, `analysis/audio/events` |
| **Stream**: fast continuous data | no | up to 30/s | `analysis/audio/fast` |

A state channel is retained so a module that starts late (or restarts) gets the current truth at once. Big state (the library, 400 KB) is fine retained; subscribers that don't need it must not subscribe with `jukebox/#`.

Commands (`cmd`) are events: `{"action": "<verb>", ...}`. A module ignores actions it doesn't know.

## Liveness

Every long-running module publishes `jukebox/<module>/heartbeat` every 2 s: `{"service": "<module>", "ts": <unix seconds>}` (`shared/bus.py` does this). A module is **up** if its last heartbeat is less than 10 s old. A module may also publish a retained `status` with `{"online": bool}` (and set a last-will to `online: false`); when present it wins over the heartbeat.

## Who may publish what

The bus has no access control yet: anything on the network can publish (Mosquitto `allow_anonymous true`). That is acceptable on a home network for the reference build and is the first item for hardening (Phase 8): per-module credentials and ACLs, so a module can publish only under its own name, plus `cmd` topics. The naming rule above is written so those ACLs can be added without renaming anything.

## Inventory: the reference build

What the reference build actually publishes, as of this version. Retained = R.

| Topic | Kind | Payload (main fields) |
| --- | --- | --- |
| `jb-input/gesture` | event | `control`, `gesture` (`turn`, `click`, `hold`), `steps` |
| `jb-input/key` | event | `key`, `kind` |
| `jb-input/action` | event | `action`, `value` |
| `jb-input/raw` | event | `control`, `state` |
| `jb-input/status` | R state | `online`, `device`, `faults` |
| `jb-core/queue` | R state | `shuffle_active`, `upcoming` |
| `jb-core/entry` | R state | `state`, `letter`, `reason` (keypad entry) |
| `jb-core/power` | R state | `state` |
| `jb-core/selection` | event | a keypad pick: slot `index`, `code` |
| `jb-core/notice`, `rejected`, `skipped` | event | messages for the screen |
| `jb-core/cmd` | command | `select` (`track`), `select_album`, `shuffle_on`, `shuffle_off`, `power` |
| `jb-audio/state` | R state | `volume` 0..1, `muted` |
| `jb-audio/cmd` | command | `volume_delta`, `mute`, `unmute`, `toggle_mute` |
| `jb-library/tracks` | R state | `tracks`: title, artist, album, year, length, cover, added_at, last_played_at |
| `jb-library/playlists` | R state | `playlists`: name, titles |
| `jb-library/albums` | R state | `albums` |
| `jb-library/cmd` | command | `favorite` (`title`) |
| `jb-source-phone/connected` | R state | `connected`, `name`, `mac` |
| `jb-source-phone/now-playing` | state | `title`, `artist`, `album`, `status`, `position`, `duration` |
| `jb-source-local/library` | state | `drives` (id, label, songs, pictures, videos, skipped, scanning), `tracks` (title, artist, album, genre, album_track, duration_ms, item_path `drive:<id>/<path>`) |
| `jb-source-local/now-playing` | event | as the phone's, plus `item_path`, and `ended: true` when a song finishes |
| `jb-source-phone/lease`, `reconnect` | R state | source internals other modules may watch |
| `jb-source-phone/crawl-result` | R state | the deep library crawl |
| `jb-source-phone/play-error` | event | a pick that failed |
| `jb-source-phone/cmd` | command | play, pause, crawl... |
| `jb-crawl/status`, `album` | R state | crawl progress |
| `jb-crawl/cmd` | command | `crawl_now`, `album` |
| `analysis/audio/fast`, `slow`, `events`, `status` | stream, R state, event, R state | see [audio-analysis.md](audio-analysis.md) |
| `<module>/heartbeat` | event | liveness, every module |
| `jb-admin/changed` | event | the owner changed settings, layouts or the theme: `{"what": [...], "at": ...}` (see [configuration.md](configuration.md)) |

## Proving notes

- Fits cleanly: every module already publishes only under its own name, uses JSON objects, and retains its state.
- Gap: only the analyzer stamps `v`. Fine under the "absent = 1" rule; new public payloads should stamp it.
- Settled with the second source (USB drives, Oct 14, 2026): sources keep their own topics; subscribers take both and follow whichever last reported `playing` (see [modules.md](modules.md), Roles).
