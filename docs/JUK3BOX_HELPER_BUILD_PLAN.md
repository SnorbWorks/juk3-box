# juk3box Helper — iOS App Build Plan

> **For Claude Code:** Work through this plan **one phase at a time**. At the end of each phase, stop, summarize what was built, list what Shawn must test on a real iPhone + Pi, and wait for confirmation before starting the next phase. BLE, MusicKit playback, AirPlay routing and hotspot joining **do not work in the iOS Simulator** — every phase ends with an on-device test.

> **Revised Oct 19, 2026** after review against the jukebox as built: a sleep test right after Phase 0 (go/no-go), a Bluetooth LE wake-up as part of the core design, the Pi side as `jb-source-helper` beside the Bluetooth source, Face ID dropped, the app in its own repo, and the open questions answered (§7).

---

## 1. What this app is (and is not)

**juk3box** is a vintage-style jukebox (Rock-Ola 435 cabinet) driven by a Raspberry Pi running a WordPress-like CMS platform. A kiosk theme renders a classic jukebox song board on the cabinet monitor. Physical selection buttons (A–K, 1–0, plus reject/reset) choose songs. Streaming services are the **back end only**.

**The helper app is a background companion, not a music player.**

- **Song selection happens on the cabinet**, not in the app. The app must NOT become an Apple Music–style browsing UI (the guest App Clip in Phase 9 is the one deliberate exception, and it mirrors the jukebox board, not Apple Music).
- **The app's jobs:**
  1. Expose the phone owner's Apple Music library (playlists, songs, albums, artists) to the Pi so it populates the song board.
  2. Play the song the jukebox selects, via MusicKit, with audio routed to the Pi over **AirPlay 2**.
  3. Report playback state and metadata back to the Pi.
  4. Onboarding (setup wizard), pairing, and a native shell around the Pi's existing admin web UI.
- **An added source, not a replacement.** Bluetooth stays: guests without the app, and the owner whenever the app isn't around, keep using the Bluetooth phone source and everything built for it (the pick doctor, the sync overlay, reconnects). The helper app is a second, more capable phone source.
- **Jukebox semantics:** the Pi owns the selection queue. The app plays **exactly one song per command**, reports when it ends, and waits for the next command. No autoplay, no shuffle-into-the-library.

### Out of scope for this app
- Pandora (no iOS control path; handled on the Pi via pianobar if ever).
- Spotify (handled Pi-side via librespot + Spotify Web API — better than the iOS App Remote SDK).
- Android (later; the Pi-side protocol is designed to be platform-neutral so an Android client can reuse it).
- Vinyl recognition / title-strip OCR (those belong to the Sidecar / Rock-Ola 454 project, which already uses Dejavu).

---

## 2. System architecture

```
┌────────────── iPhone ──────────────┐            ┌──────────────── Raspberry Pi ────────────────┐
│ juk3box Helper (SwiftUI)           │            │                                              │
│  • MusicKit (library + playback)   │──AirPlay 2─▶ shairport-sync (AirPlay 2 receiver) ─▶ DAC   │
│  • ApplicationMusicPlayer          │            │                                              │
│  • Bonjour discovery (NWBrowser)   │◀─WebSocket─▶ jb-source-helper (new) ◀─MQTT─▶ jb-core, UI  │
│  • WKWebView → admin web UI        │──HTTP────▶  existing admin web UI + artwork upload        │
│  • NEHotspotConfiguration (setup)  │──Wi-Fi───▶  setup hotspot + captive wizard (setup mode)   │
└────────────────────────────────────┘            └──────────────────────────────────────────────┘
```

- **Audio:** AirPlay 2 (lossless ALAC) to shairport-sync on the Pi, into PipeWire like the Bluetooth audio, so it goes through the whole-home delay filter to the DAC and, with whole-home on, back out to the house's AirPlay zone. That's about 2 s of AirPlay buffer in plus the 1.4 s whole-home delay: roughly 3.5 s from a pick to sound, which the owner is fine with (Oct 19); the cabinet's buttons, lights and screen react at once. Bluetooth A2DP/AVRCP stays as the other phone source (existing Pi work, not part of this app).
- **Staying reachable (core, not optional):** iOS suspends an app soon after its audio stops, and may suspend it even while MusicKit plays (playback can run outside the app's process). A suspended app's WebSocket is gone, so the Pi can't send the next `play`. The Pi therefore also advertises a small **Bluetooth LE** service; the app connects to it with the `bluetooth-central` background mode, and iOS wakes the app when the Pi sends a notification ("wake: a command is waiting"), so it reconnects its WebSocket and takes the command. BLE is light on the radio next to Bluetooth Classic browsing.
- **Control:** JSON over WebSocket on the local network. Pi advertises `_juk3box._tcp` via Bonjour/Avahi.
- **Artwork:** MusicKit library artwork can resolve to non-fetchable `musicKit://` URLs, so the **app renders artwork to JPEG and uploads it** to the Pi over HTTP.
- **Admin:** the existing Pi-served admin web UI, shown in a `WKWebView`. Do not rebuild admin natively.
- **Setup:** Pi falls back to a setup hotspot when it has no known network; the setup wizard is web pages served by the Pi. The app joins the hotspot itself with `NEHotspotConfiguration` so the user never opens Settings.

---

## 3. Tech stack & conventions

| Item | Choice |
|---|---|
| Minimum iOS | 17.0 (Observation framework, mature MusicKit) |
| UI | SwiftUI, `@Observable` models |
| Concurrency | Swift Concurrency (async/await, actors) |
| Project file | **XcodeGen** (`project.yml`) — never hand-edit `.pbxproj` |
| Networking | `URLSessionWebSocketTask`, `NWBrowser` (Network framework) |
| Music | MusicKit (`MusicLibraryRequest`, `ApplicationMusicPlayer`) |
| Secrets | Keychain (device tokens per paired jukebox) |
| Tests | XCTest for protocol codec, state machines, reconnection logic |
| Build | `xcodebuild` from the CLI; Shawn installs to device from Xcode |

**Required Info.plist / capabilities**
- `NSAppleMusicUsageDescription`
- `NSLocalNetworkUsageDescription` + `NSBonjourServices` = `["_juk3box._tcp"]`
- Background Modes → **Audio, AirPlay, and Picture in Picture**; **Uses Bluetooth LE accessories** (`bluetooth-central`) for the wake-up
- `NSBluetoothAlwaysUsageDescription` (the wake-up)
- Hotspot Configuration capability (Phase 5)
- App Groups (Phase 8, shared with widget / Live Activity extensions)
- App Clip target + Associated Domains (Phase 9)

**Code layout.** The app lives in **its own repo**, `juk3box-helper` (on the Mac at `~/Desktop/juk3box-helper`; bundle ID `com.our-millionaireideas.juk3boxhelper`, team BLN2JP24KQ). The Pi side (`jb-source-helper`) lives in `juk3-box` with the other services, and the protocol below is copied into `juk3-box/docs/contracts/` as the shared contract.

**Tooling:** this MacBook Pro is Intel, so Xcode 26.3 is the newest it runs (Xcode 27 needs Apple silicon and macOS 26.6). Fine for building to a phone; App Store uploads need an Apple silicon Mac once Apple requires the Xcode 27 SDK (likely spring 2027).
```
juk3box-helper/
  project.yml
  App/                 # app entry, root navigation
  Core/Protocol/       # message types, codec, versioning
  Core/Connection/     # discovery, WebSocket client, reconnection, pairing
  Core/Music/          # MusicKit auth, library sync, playback engine
  Core/Setup/          # hotspot join, setup flow state machine
  Features/Home/       # status screen (connected jukebox, now playing)
  Features/Admin/      # WKWebView shell + auth injection
  Extensions/          # widget, Live Activity, App Clip (later phases)
  Tests/
tools/mock-pi/         # mock Pi server for development without hardware
```

---

## 4. Pi ↔ App protocol (contract)

Define this first. Both sides code against it. Every message is a JSON object:

```json
{ "v": 1, "type": "play", "id": "c0a1…", "payload": { … } }
```
- `v` = protocol version. `id` = UUID for request/response correlation. Responses echo `id` with `type: "ack"` or `type: "error"`.

### Pi → App (commands)
| type | payload | meaning |
|---|---|---|
| `hello` | `{ jukeboxName, jukeboxId, protocolVersions }` | sent on connect |
| `pair_challenge` | `{ }` | Pi is showing a 4-digit code on the cabinet screen |
| `request_library` | `{ since?: hash }` | full or incremental library sync |
| `play` | `{ itemId, kind: "song" }` | play exactly this item now |
| `stop` | `{ }` | stop playback |
| `pause` / `resume` | `{ }` | |
| `set_active` | `{ active: bool }` | Pi switched the active phone (multi-phone) |
| `request_artwork` | `{ itemIds: [], size: 600 }` | upload artwork for these items |

### App → Pi (events)
| type | payload | meaning |
|---|---|---|
| `client_hello` | `{ deviceName, deviceId, appVersion, token? }` | identifies the phone (shown on the phone tab) |
| `pair_response` | `{ code }` | user entered the cabinet code → Pi replies with a device token |
| `library_chunk` | `{ seq, total, kind, items: [] }` | paged library data (≤500 items/chunk) |
| `library_done` | `{ hash, counts }` | sync complete |
| `playback_state` | `{ itemId, state, position, duration, title, artist, album }` | sent on change + every 1s while playing |
| `track_ended` | `{ itemId }` | song finished → Pi decides what plays next |
| `play_failed` | `{ itemId, reason }` | e.g. not playable / no subscription / offline |

### Library item shape
```json
{ "id": "i.abc123", "kind": "song", "title": "…", "artist": "…", "album": "…",
  "duration": 214, "playlistIds": ["p.xyz"], "dateAdded": "…", "playCount": 12,
  "artworkKey": "sha1-of-item-id" }
```
Playlists, albums and artists are sent as their own kinds with member `songIds`. The board's sort options (title, artist, album, most recent, most popular) must be computable from these fields.

### HTTP endpoints on the Pi
- `POST /api/phone/artwork/{artworkKey}` — JPEG body, authenticated with device token.
- `GET /admin/…` — existing admin UI (WKWebView target).
- Setup mode only: `GET /setup/…` — setup wizard pages.

---

## 5. Phases

### Phase 0 — Accounts & project scaffold *(Shawn does the account steps)*
**Shawn (manual):**
- Apple Developer Program account ($99/yr).
- Register bundle ID (e.g. `com.juk3box.helper`); enable **MusicKit** under App Services for that ID.
- Confirm an Apple Music subscription on the test iPhone.

**Claude Code:**
- XcodeGen `project.yml`, app target, test target, Info.plist keys and Background Audio capability from §3.
- Empty SwiftUI app that launches on device.

**Done when:** app installs and launches on Shawn's iPhone from Xcode.

*Progress (Oct 19): membership renewed; scaffold built in `juk3box-helper` and signed by automatic signing; Xcode 26.3 installed; phone paired. Left: Xcode's first launch, Developer Mode on the phone, register the App ID with MusicKit, install.*

---

### Phase 0b — Sleep test (go/no-go) *(before anything else is built)*
The whole design depends on the Pi reaching the app when nothing is playing. Prove it first, with the least code:
- The bare app with MusicKit auth, `ApplicationMusicPlayer` and a WebSocket client; a ~20-line Python script on the Pi (or the Mac) that sends `play`.
- Script: play a song → let it end → wait 5 minutes with the phone **locked** → play again. Then the same with the app swiped to the background, and with the BLE wake-up added.
- Log on the Pi when each command lands and when audio starts.

**Done when:** the second `play` reliably lands with the phone locked after 5 minutes, or we know exactly what keeps the app reachable (BLE wake-up, keeping the audio session alive) and the design is updated to match.

---

### Phase 1 — Protocol + mock Pi server
- Implement message types and codec in `Core/Protocol` with unit tests (encode/decode round-trips, unknown-type tolerance, version negotiation).
- Build `tools/mock-pi/` (Python, `websockets` + `zeroconf`): advertises `_juk3box._tcp`, speaks the protocol, logs everything, has a tiny CLI/REPL to send `play`, `stop`, `request_library`, and accepts artwork uploads to a local folder.

**Done when:** unit tests pass; mock server runs on a Mac or Pi and is discoverable via `dns-sd -B _juk3box._tcp`.

---

### Phase 2 — Discovery, connection, pairing
- `NWBrowser` discovery of `_juk3box._tcp`; list found jukeboxes.
- WebSocket client actor with exponential-backoff reconnection, heartbeat/ping, and clean resume.
- Pairing: Pi shows a 4-digit code on the cabinet → user enters it → token stored in Keychain → future connects send the token in `client_hello`.
- **BLE wake-up** (as proven in Phase 0b): connect to the Pi's BLE service and stay connected in the background; on its notification, reconnect the WebSocket and take the waiting command.
- Home screen: connected jukebox name, connection state, this phone's name as the Pi will display it.

**Done when (on device, against mock-pi):** app finds the mock jukebox, pairs once, reconnects automatically after the server restarts and after the app is backgrounded and reopened.

---

### Phase 3 — Music control MVP ⭐ *(proves the architecture)*
- MusicKit authorization flow with clear failure states (denied, no subscription).
- Playback engine around `ApplicationMusicPlayer`:
  - On `play(itemId)`: resolve the library song, set a **single-item queue**, play.
  - Emit `playback_state` on change and every 1s while playing.
  - Emit `track_ended` when the song finishes; then **stay stopped** (jukebox semantics).
  - Handle `stop`, `pause`, `resume`, and `play_failed` cases.
- AirPlay route: embed `AVRoutePickerView` on the Home screen with a clear prompt ("Select juk3box as your speaker"). Show the current route name if available.
- Background audio enabled so control stays alive while playing.

**Pi prerequisite (Shawn / Pi-side Claude Code session):** shairport-sync built with AirPlay 2 (`--with-airplay-2`, `nqptp`), advertised as "juk3box", output to the DAC.

**Done when:** from the mock-pi REPL (hardcoded test song ID), `play` starts the song, audio comes out of the jukebox over AirPlay, `track_ended` arrives at the end, and this still works with the iPhone locked.

---

### Phase 4 — Library sync & artwork
- Fetch playlists, songs, albums, artists via `MusicLibraryRequest` with paging.
- Stream to the Pi as `library_chunk` messages; send `library_done` with a content hash.
- Incremental sync: if the Pi's `since` hash matches, skip; otherwise resend (v1 can resend everything, but keep the hash plumbing).
- Artwork: on `request_artwork`, render each item's artwork to a 600px JPEG and `POST` it. Throttle uploads (max 4 concurrent), skip already-uploaded keys.
- Test with a large library (target: 10,000 songs, sync < 60s on home Wi-Fi).

**Done when:** mock-pi receives the full library + artwork; a second sync with no changes is near-instant.

---

### Phase 5 — Pi-side `jb-source-helper` *(in the juk3-box repo)*
A new Python service beside `jb-source-phone`, on the MQTT bus like the others (`shared.bus`, deploy with `deploy/deploy.sh`). It publishes **the same topics and shapes the Bluetooth source does** (connected, sync, now-playing, play-error, pick-problem; handles `play_item`), so jb-core, the pick doctor, the sync overlay, "Please connect a device", the lights and the admin work with it unchanged.
- Replace mock-pi with the real service: Avahi advertisement, WebSocket server, the BLE wake-up service, pairing (code shown on the kiosk), device token store, and phones in the existing phone registry (Settings > Bluetooth and the admin's phone page).
- Library ingest into the CMS data layer so the kiosk theme's song board renders phone content under the phone source tab (shows the connected phone's name).
- Button selection → `play` command; `track_ended` → next queued selection or idle.
- Artwork endpoint storing files where the theme can serve them.
- Multi-phone: list paired phones, switch active phone (even mid-song: `stop` to old, `set_active` to new).

**Done when:** press a selection on the cabinet → that song plays from the phone over AirPlay, with the board highlighting it and showing artwork.

---

### Phase 6 — Setup wizard (hotspot onboarding)
**Pi side:** setup mode — when no known Wi-Fi is reachable (or on a long-hold reset gesture), bring up a hotspot (e.g. `juk3box-setup-XXXX`, NetworkManager AP mode) and serve `/setup/` wizard pages (choose home Wi-Fi, enter password, name the jukebox). Then join home Wi-Fi and advertise normally.

**App side:**
1. "Set up a new juk3box" → join the setup hotspot via `NEHotspotConfiguration`.
2. Show `/setup/` in a WKWebView; user completes Wi-Fi + naming.
3. App leaves the hotspot (`removeConfiguration`), rejoins home Wi-Fi, discovers the jukebox via Bonjour.
4. Pairing (Phase 2 flow) → MusicKit authorization → AirPlay route step → first library sync.
- Without the app (Android/any phone), the same web wizard works by joining the hotspot manually.

**Done when:** a freshly flashed Pi goes from boxed to playing a song with no SSH and no visit to iOS Settings.

---

### Phase 7 — Admin shell
- Admin tab: WKWebView pointed at the jukebox's admin (jb-admin, port 8080), signing in with the admin's own owner password as in a browser. **No Face ID and no token sign-in** (decided Oct 19: not needed).

**Done when:** the Admin tab opens the existing admin and the owner can sign in.

---

### Phase 8 — Reliability, then lock screen & Siri
**Reliability first:**
- Soak test: 4-hour session of repeated cabinet selections; zero dropped commands.
- Handle: Wi-Fi blips, Pi reboots, AirPlay route lost, phone call interruptions (`AVAudioSession` interruption handling), app killed by iOS.
- When the app is suspended and idle, the Pi must show the phone as "asleep — open juk3box Helper" instead of failing silently.

**Then system integration:**
- **Live Activity** (+ Dynamic Island): now playing on the jukebox, with a stop button.
- **Widget:** jukebox status + now playing.
- **App Intents / Siri:** "Play [playlist] on the jukebox" (sends the playlist's songs to the Pi's selection queue), "What's playing on the jukebox?", "Stop the jukebox."

**Done when:** soak test passes; Live Activity updates within ~2s of a cabinet selection; Siri intents work from the lock screen.

---

### Phase 9 — Guest App Clip (NFC / QR queueing)
- App Clip target, launched from an NFC tag or QR code on the cabinet (Associated Domains + App Clip Experience).
- Guest flow: see the jukebox's current board (fetched from the Pi, reusing a kiosk-theme page in a web view where possible) → pick a song → added to the Pi's selection queue.
- Pi side: guest queue endpoint with rate limiting and owner controls (enable/disable guest mode, max queued per guest, approve-before-play option) in the admin UI.
- Guests never get admin access or the owner's device token.

**Done when:** a phone that has never installed the app taps the cabinet tag and queues a song in under 15 seconds.

---

### Phase 10 — Paid plays (commercial venues) *(deferred; business decision first)*
- Apple Pay in the App Clip for paid selections (credit packs like coin play).
- Requires: merchant ID, a payment processor (e.g. Stripe) with a small server component, and a legal and licensing check — **public performance licensing for venues is a separate issue from payments**. Do not build until Shawn decides on the commercial path.

---

### Phase 11 — TestFlight & App Store
- App icon, privacy nutrition label (library data stays on the local network — state this clearly), App Review notes explaining the hardware dependency, plus a demo video for reviewers.
- TestFlight build to friends with jukeboxes (or with mock-pi) before submission.

---

## 6. Risks & things to verify early

| Risk | Mitigation / check |
|---|---|
| AirPlay route can't be set in code | User selects once via `AVRoutePickerView`; detect and prompt if the route is lost. |
| iOS suspends the app when idle | **Tested first (Phase 0b).** BLE wake-up is part of the core design; the Pi shows a "phone asleep" state when it can't reach the app. |
| Library artwork URLs not fetchable remotely | App renders and uploads JPEGs (Phase 4). |
| Catalog items in library need a subscription | Surface `play_failed` with a clear reason on the cabinet screen. |
| AirPlay ~2s buffer | Cabinet UI reacts to button presses instantly (LEDs, board highlight); only audio lags. Never use the stream for UI sound effects. |
| Pi 4 vs Pi 5 headroom | The Pi 5 is here (move planned). Measure CPU/RAM with shairport-sync + the kiosk + jb-source-helper + whole-home running together on it. |
| AirPlay in and AirPlay out at once | The receiver (shairport-sync) and the whole-home sender share PipeWire; check the delay filter and sync with both running. |
| App Review rejects a hardware-only app | Detailed review notes + demo video; mock mode for reviewers. |

---

## 7. Open questions for Shawn
1. ~~Pi 4 or Pi 5?~~ The Pi 5 is here; the move comes before the Pi side of this app (Oct 19).
2. ~~CMS stack and repo?~~ Python services on an MQTT bus, in the `juk3-box` repo (`services/`, `shared/bus.py`, `deploy/deploy.sh`). The Pi side is `jb-source-helper` there.
3. ~~Feed the house speakers?~~ Already built: whole-home audio sends the cabinet out to an AirPlay zone in sync (Oct 18). The helper's AirPlay audio goes through the same path.
4. App name on the App Store: "juk3box Helper" or "juk3box"? *Still open.*
5. ~~Where does the app live?~~ Its own repo, `juk3box-helper` (Oct 19).
