# Contract: the helper app protocol

Version 1 · **Draft, built Oct 20, 2026, not yet proven on a phone** · the iOS app `juk3box-helper` ↔ `jb-source-helper` on the jukebox

The phone app (`Juk3box Helper`) is a second phone source beside Bluetooth: the jukebox picks the song, the app plays it from the owner's Apple Music library with MusicKit, and the sound comes to the cabinet over AirPlay. This is everything the two sides say to each other. The plan and its reasons are in [JUK3BOX_HELPER_BUILD_PLAN.md](../JUK3BOX_HELPER_BUILD_PLAN.md).

## Finding the jukebox

- Bonjour service `_juk3box._tcp`, port **8770**. TXT: `id` (the jukebox's id, stable), `name` (shown to the owner), `v` (`1`), `path` (`/helper`).
- WebSocket at `ws://<host>:8770/helper`. Text frames, one JSON message each.
- HTTP on the same port for artwork (below).

## Messages

Every message is a JSON object:

```json
{ "v": 1, "type": "play", "id": "6f1c…", "payload": { … } }
```

- `v`: protocol version (1). `type`: below. `payload`: an object (may be `{}`).
- `id`: a UUID string on every **command** (Pi → app) and on the app's answer to it; absent on events.
- The app answers each command with `ack` (`payload` may carry results) or `error` (`payload`: `reason`, `message`), echoing the command's `id`.
- Unknown `type`s and unknown fields are ignored on both sides (log them).

## Connecting and pairing

1. App opens the WebSocket. Pi sends `hello`:
   `{ "jukeboxName": "Juk3box", "jukeboxId": "…", "protocolVersions": [1], "ble": { "service": "<uuid>", "wake": "<uuid>" } }`
2. App sends `client_hello`:
   `{ "deviceName": "Alex's iPhone", "deviceId": "<uuid the app keeps>", "appVersion": "0.1", "token": "<if paired before>", "model": "iPhone16,1" }`
3. Pi answers:
   - a known token for that `deviceId`: `welcome { "deviceId", "active": bool }`. Done. `active` is false when another phone (Bluetooth or another app) has the jukebox: the app stays connected and its library is kept, and the cabinet asks "Alex's iPhone is here. Switch to it?" (or the owner switches under Sources). `set_active { "active": true }` arrives when it's chosen.
   - an unknown phone while pairing is turned off (the admin's Sources > App, "Allow new phones to pair"): `pair_closed { "message": "…" }`, then the Pi closes. The app shows the message ("Pairing is off on Juk3box. Turn it on in the jukebox admin: Sources > App.") and retries with its usual backoff, so it pairs as soon as pairing is turned on.
   - otherwise `pair_challenge { "digits": 4, "expires": <seconds> }` and the cabinet shows a 4-digit code with the phone's name. The app asks the owner for it and sends `pair_response { "code": "1234" }`. Right: `paired { "token": "…" }` then `welcome`. Wrong: `pair_failed { "triesLeft": n }`; after 3 wrong tries or when it expires, a new `pair_challenge` (and a new code on the cabinet).
4. The app keeps the token in the Keychain, per `jukeboxId`.

The Pi pings over the WebSocket every 15 s (standard ping frames); the app's WebSocket client answers them. Either side may close; the app reconnects with backoff (1, 2, 4 … 30 s).

## Pi → app (commands)

| type | payload | the app does |
| --- | --- | --- |
| `request_library` | `{ "since": "<hash>"? }` | sends the library (below); if its hash equals `since`, just `library_done` with that hash and no chunks |
| `play` | `{ "itemId": "…", "kind": "song" }` | plays exactly that song, alone (a one-song queue; no autoplay after). `ack` when it starts, or `error` with a `play_failed` reason |
| `stop`, `pause`, `resume` | `{}` | as named |
| `set_active` | `{ "active": bool }` | this phone is (or isn't) the jukebox's phone source now; an inactive app stops playing and stays connected |
| `request_artwork` | `{ "keys": ["…"], "size": 600 }` | uploads those artworks (below), at most 4 at a time; `ack` when done with `{ "uploaded": n, "missing": [keys] }` |

## App → Pi (events)

| type | payload |
| --- | --- |
| `library_chunk` | `{ "seq": 0, "total": 12, "kind": "song" or "playlist", "items": [ … ≤ 500 ] }` (all songs first, then playlists; `seq` counts across both) |
| `library_done` | `{ "hash": "<sha256 hex>", "counts": { "song": n, "playlist": n } }` |
| `playback_state` | `{ "itemId", "state": "playing" / "paused" / "stopped", "position": s, "duration": s, "title", "artist", "album" }`, on every change and every second while playing |
| `track_ended` | `{ "itemId" }`: the song reached its end; the app stays stopped |
| `play_failed` | `{ "itemId", "reason", "message" }`, reasons: `not_found` (not in the library), `not_playable` (e.g. a cloud song unavailable), `no_subscription`, `not_authorized` (Music access denied), `offline`, `other` |
| `music_status` | `{ "authorization": "authorized" / "denied" / "notDetermined" / "restricted", "canPlay": bool, "canPlayCatalog": bool (optional), "route": { "airplay": bool, "kind": "airplay" / "bluetooth" / "phone" / "other" (optional), "name": "Juk3box" } }`, on connect and when any of it changes (so the cabinet can say "choose Juk3box as the speaker"). `canPlay`: the owner's library can play (Music access allowed; library songs need no subscription). `canPlayCatalog`: songs added from Apple Music can play (a subscription); absent while unknown. `route.airplay` is true for AirPlay to any speaker; compare `name` to tell the jukebox |

### Library items

```json
{ "id": "i.abc123", "title": "Moon River", "artist": "Andy Williams", "album": "Moon River & Other Great Movie Themes",
  "albumArtist": "Andy Williams", "genre": "Pop", "duration": 164.2, "trackNumber": 1, "discNumber": 1,
  "year": 1962, "dateAdded": "2024-03-01T12:00:00Z", "lastPlayed": "2026-10-01T20:00:00Z", "playCount": 12,
  "artworkKey": "3f2a…" }
```

- `id` is the MusicKit library id (`Song.id`); `play` uses it.
- `artworkKey`: sha1 hex of `lowercase(albumArtist or artist) + "\n" + lowercase(album)`, so one album's songs share one upload. Absent when the song has no artwork.
- Playlists: `{ "id": "p.xyz", "name": "Road Trip", "songIds": ["i.abc123", …] }`.
- Albums and artists aren't sent: the jukebox builds them from the songs.
- `hash`: sha256 hex of the songs then the playlists, each sorted by `id`, as compact JSON (sorted keys, no spaces), one per line. The Pi stores it and sends it as `since` next time.

## Artwork upload

`POST http://<host>:8770/artwork/<artworkKey>` with `Authorization: Bearer <token>`, `Content-Type: image/jpeg`, a square JPEG of the requested size, at most 2 MB. `204` stored, `401` bad token, `413` too big, `400` bad key (40 hex chars).

## Waking the app (Bluetooth LE)

iOS suspends an idle app, so its WebSocket goes away; the Pi wakes it over Bluetooth LE:

- The Pi is a BLE peripheral advertising service `4A554B45-0001-4C80-8000-6A756B33626F` (**juk3box helper**) while at least one helper phone is paired.
- Characteristic `4A554B45-0002-4C80-8000-6A756B33626F` (**wake**: read, notify): a 4-byte little-endian counter. The Pi bumps it and notifies when it has a command for a phone that isn't connected over the WebSocket.
- Characteristic `4A554B45-0003-4C80-8000-6A756B33626F` (**id**: read): the jukebox id (UTF-8), so the app knows which jukebox it found.
- The app (background mode `bluetooth-central`) keeps a connection to its jukebox's peripheral, subscribed to **wake**; on a notification it reconnects the WebSocket. When the BLE link drops it calls `connect` again (iOS completes it whenever the jukebox is back in range, even with the app suspended).

The Pi holds a command for up to 20 s for its phone to reconnect; after that the pick fails as `no_answer` (the pick doctor's wording: "open Juk3box Helper on your phone").

## On the jukebox's bus

`jb-source-helper` is a `source` (see [modules.md](modules.md), Roles). Its songs' `item_path` is `helper:<deviceId>/<itemId>`; jb-core sends their picks to it. It publishes, in the Bluetooth source's shapes: `jukebox/jb-source-helper/connected`, `sync`, `now-playing`, `play-error`, `pairing` (the cabinet's code), and `library` (the phone's songs for jb-library). Details in [bus.md](bus.md).
