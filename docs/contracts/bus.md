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
| `jb-core/source` | R state | which phone has the jukebox, Bluetooth or the helper app (`services/jb_core/phone_choice.py`): `active` (`kind` `phone`/`helper`, `id` a Bluetooth address or `helper:<device>`, `name`, `connected`) or null, and `offer` (a phone that arrived meanwhile, which the cabinet offers) or null. jb-library shows that phone's songs; jb-source-helper makes that app phone its source; while the app has it, jb-source-phone doesn't reconnect Bluetooth, and disconnects a Bluetooth phone when the app takes over (so the iPhone's audio can go to AirPlay) |
| `jb-core/entry` | R state | `state`, `letter`, `reason` (keypad entry) |
| `jb-core/power` | R state | `state` |
| `jb-core/selection` | event | a keypad pick: slot `index`, `code` |
| `jb-core/notice`, `rejected`, `skipped` | event | messages for the screen |
| `jb-core/pick-problem` | event | a pick that didn't play, from the pick doctor (`services/jb_core/pick_doctor.py`): `title`, `artist`, `problem` (`no_controls`, `wrong_app` (the phone is presenting another app, e.g. Messages, instead of Music; also sent as a `note` when the phone connects like that), `no_answer`, `not_found`, `no_start`, `muted`, `no_sound`, `stuck` (picks failing one after another: shuffle is turned off and the queue stops), or `retrying` while the source heals it), `headline`, `advice`, `retry` (the song waits at the front of the queue for the phone to come back) |
| `jb-core/pick-log` | R state | `problems`: the last 20 pick problems, newest first, each with `at` (epoch seconds); the admin dashboard shows them |
| `jb-core/cmd` | command | `select` (`track`), `select_album`, `shuffle_on`, `shuffle_off`, `power`, `use_source` (`id`: give the jukebox to that phone, connecting a Bluetooth one), `decline_source` (not now, for the offer) |
| `jb-audio/state` | R state | `volume` 0..1, `muted` |
| `jb-audio/cmd` | command | `volume_delta`, `mute`, `unmute`, `toggle_mute` |
| `jb-display/state` | R state | `inset` (top, right, bottom, left px; the kiosk page fits itself inside them), `rotation`, `sleep_minutes`, `asleep`, `guide`, `output`, `limits`, `sway` |
| `jb-display/cmd` | command | `inset` (`side` + `px`, or `inset`), `inset_reset`, `rotation` (`degrees`), `sleep` (`minutes`), `wake`, `guide` (`on`) |
| `jb-library/drives-in` | R state | `drives`: the USB drives plugged in that have songs (`id`, `label`, `song_count`), for the cabinet's Sources |
| `jb-library/tracks` | R state | `tracks`: title, artist, album, year, length, cover, added_at, last_played_at |
| `jb-library/playlists` | R state | `playlists`: name, titles |
| `jb-library/albums` | R state | `albums` |
| `jb-library/cmd` | command | `favorite` (`title`) |
| `jb-source-phone/connected` | R state | `connected`, `name`, `mac`, `app` (the app the phone presents: "Music", "Messages"...), `browsable` (its song list is offered), `controls: false` when connected for audio only |
| `jb-source-phone/sync` | R state | where the phone's song list stands: `state` (`none`, `connecting`, `syncing`, `ready`, `failed`, `wrong_app`), `name`. jb-core holds the phone's songs while it's `connecting`, `syncing` or `wrong_app` and plays them when it's `ready`; the screen shows "Connecting and updating library" meanwhile |
| `jb-source-phone/now-playing` | state | `title`, `artist`, `album`, `status`, `position`, `duration` |
| `jb-source-helper/connected` | R state | the Juk3box Helper app ([helper-protocol.md](helper-protocol.md)): `connected` (reachable: the app connected, or asleep but able to be woken over Bluetooth LE), `name`, `device_id`, `awake` (its WebSocket is up), `music` (`authorization`, `canPlay`, `canPlayCatalog`, `route`), `phones` (every paired app phone: `device_id`, `name`, `reachable`, `awake`, `songs`, `synced_at`, `last_seen`). `device_id` is null while a Bluetooth phone has the jukebox |
| `jb-source-helper/sync` | R state | as the phone's: `state` (`none`, `syncing`, `ready`, `failed`), `name` |
| `jb-source-helper/library` | R state | the active app phone's library: `device_id`, `name`, `hash`, `tracks` (title, artist, album, genre, album_track, duration_ms, year, added_at, artwork_key, item_path `helper:<device>/<item>`), `playlists` (name, titles) |
| `jb-source-helper/now-playing` | state | as the phone's, plus `item_path`, and `ended: true` when a song finishes (the app stops after each song) |
| `jb-source-helper/play-error` | event | `item_path`, `title`, `reason` (`no_answer`, `not_found`, `not_playable`, `no_subscription`, `not_authorized`, `offline`, `other`) |
| `jb-source-helper/pairing` | R state | `state` (`code` or `none`), `code`, `device`, `expires`: the cabinet shows the code to type in the app |
| `jb-source-helper/settings` | R state | the admin's Sources > App: `allow_pairing` (off once a phone pairs; unknown apps then get `pair_closed`), `ble` (wake-up), `name` (the jukebox's name in the app and as the AirPlay speaker) |
| `jb-source-helper/artwork` | event | `at`: covers from the app landed (once a batch stops arriving); the screen reloads the cover images that failed before they were here |
| `jb-source-helper/forgotten` | event | `mac`: `helper:<device>` (jb-library forgets its songs) |
| `jb-source-helper/cmd` | command | `play_item` (`item_path`), `play`, `pause`, `stop`, `forget` (`device_id`), `rename` (`device_id`, `name`), `sync` (the active phone's library again, in full), `settings` (`allow_pairing`, `ble`, `name`) |
| `jb-source-local/library` | state | `drives` (id, label, songs, pictures, videos, skipped, scanning), `tracks` (title, artist, album, genre, album_track, duration_ms, item_path `drive:<id>/<path>`) |
| `jb-source-local/now-playing` | event | as the phone's, plus `item_path`, and `ended: true` when a song finishes |
| `jb-source-phone/lease`, `reconnect` | R state | source internals other modules may watch |
| `jb-source-phone/crawl-result` | R state | the deep library crawl |
| `jb-source-phone/play-error` | event | a pick that failed: the `play_item` command, plus `reason` (`not_found`, `no_answer`, `no_controls`) |
| `jb-source-phone/pick-problem` | event | what went wrong with a pick while the source deals with it: `title`, `item_path`, `reason` (or null; `wrong_app` keeps the pick until Music is back, with no reconnect), `retrying`, `app` (re-syncing, or reconnecting the phone with the pick kept) |
| `jb-source-phone/cmd` | command | play, pause, crawl... |
| `jb-crawl/status`, `album` | R state | crawl progress |
| `jb-crawl/cmd` | command | `crawl_now`, `album` |
| `analysis/audio/fast`, `slow`, `events`, `status` | stream, R state, event, R state | see [audio-analysis.md](audio-analysis.md) |
| `<module>/heartbeat` | event | liveness, every module |
| `jb-admin/changed` | event | the owner changed settings, layouts or the theme: `{"what": [...], "at": ...}` (see [configuration.md](configuration.md)) |
| `jb-admin/drive-offers` | R state | drives with new pictures or videos, to ask before importing them: `{"offers": [{"id", "label", "pictures", "videos"}]}`. A drive answered "not now" leaves the list until it's plugged in again |
| `jb-admin/cmd` | command | the cabinet's answer: `{"action": "import_drive" or "decline_drive", "id": drive id}` |

## Proving notes

- Fits cleanly: every module already publishes only under its own name, uses JSON objects, and retains its state.
- Gap: only the analyzer stamps `v`. Fine under the "absent = 1" rule; new public payloads should stamp it.
- Settled with the second source (USB drives, Oct 14, 2026): sources keep their own topics; subscribers take both and follow whichever last reported `playing` (see [modules.md](modules.md), Roles).
