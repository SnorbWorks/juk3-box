# Contract: Apple Music (the catalog)

Version 1 · **Draft, 2026-11-14** · the Helper app ↔ `jb-source-helper` ↔ the kiosk engine and the admin

The owner's phone has an Apple Music subscription, so the jukebox can browse **Apple Music itself**: its genres, the charts (top playlists, albums and songs, overall or for one genre), and any chart playlist's or album's songs. Songs picked there play through the Helper app like library songs. The jukebox has no Apple account; every Apple Music request goes through the app (MusicKit, the owner's subscription). The plan and its reasons are in [../Apple Music — Plan.md](../Apple%20Music%20—%20Plan.md). This contract extends [helper-protocol.md](helper-protocol.md).

## 1. Helper protocol additions (app ↔ jb-source-helper)

### The app says it can

`client_hello` gains `"features": ["catalog"]`. An app without it (the App Store 1.0 build) is never sent the commands below: the jukebox treats Apple Music as unavailable (`reason: "app_update"`).

### Pi → app commands (answered with `ack`, results in its payload, or `error`)

| type | payload | the app answers with |
| --- | --- | --- |
| `catalog_genres` | `{}` | `{ "storefront": "us", "genres": [ { "id": "21", "name": "Rock", "playlist": <Playlist> or null } ] }`: Apple's top-level genres (children of the root genre "Music", id 34; the root itself is left out), in Apple's order. `playlist` is that genre's #1 chart playlist (`MusicCatalogChartsRequest(genre:, kinds: [.mostPlayed], types: [Playlist.self])`, limit 1), whose artwork is the genre's tile. The app may fetch those concurrently (at most 6 at once). |
| `catalog_charts` | `{ "genre": "21" or null, "limit": 25 }` | `{ "playlists": [<Playlist>], "albums": [<Album>], "songs": [<Song>] }`: the most-played charts, for that genre or overall (null). `limit` per kind, at most 50. |
| `catalog_items` | `{ "kind": "playlist" or "album", "id": "pl.…" }` | `{ "id", "name", "artist": "…" or null, "artworkUrl", "songs": [<Song>] }`: that playlist's or album's songs, in order (at most 300). Music videos and anything that isn't a song are left out. |
| `play` | `{ "itemId": "catalog:<song id>", "kind": "song" }` | As before. An `itemId` starting `catalog:` is an Apple Music catalog song id: the app fetches it (`MusicCatalogResourceRequest<Song>`) and plays it alone. `playback_state`, `track_ended` and `play_failed` carry the same `itemId` back. Reasons as before: `no_subscription` when the phone has none, `not_found` when the id is gone, `offline`. |

Errors: `error { reason, message }` with `reason` one of `no_subscription`, `not_authorized`, `offline`, `other`.

### Item shapes

All artwork is a **URL** (`Artwork.url(width: 600, height: 600)`), an `https://` link on Apple's image servers. The jukebox downloads it itself; the app uploads nothing for the catalog.

```json
Song:     { "id": "1440833098", "title": "Uprising", "artist": "Muse", "album": "The Resistance",
            "genre": "Alternative", "duration": 304.8, "explicit": false, "artworkUrl": "https://…/600x600bb.jpg" }
Album:    { "id": "1440832958", "name": "The Resistance", "artist": "Muse", "explicit": false, "artworkUrl": "https://…" }
Playlist: { "id": "pl.6bf4415b83ce4f3789614ac4c3675740", "name": "Rock Hits", "curator": "Apple Music Rock", "artworkUrl": "https://…" }
```

`artworkUrl` may be null. `explicit` is `contentRating == .explicit`. `genre` is the first of `genreNames` that isn't "Music", or null.

## 2. On the jukebox's bus (jb-source-helper)

### Settings

`jukebox/jb-source-helper/settings` (retained) gains `apple_music`:

```json
"apple_music": { "on": false, "clean": false, "genres": null, "refresh_hours": 24 }
```

- `on`: the owner's switch (Sources › Apple Music). Off by default. Off hides every Apple Music entry point on the cabinet.
- `clean`: true hides explicit songs and albums everywhere Apple Music shows (default false: explicit songs show, as on the phone).
- `genres`: null = every genre, in Apple's order; else the owner's ordered list of genre ids to show.
- `refresh_hours`: how long fetched lists are kept before they're fetched again (6, 24 or 168).

Changed with `jukebox/jb-source-helper/cmd` `{ "action": "settings", "apple_music": { …any of those keys } }` (merged into the stored value).

### Status

`jukebox/jb-source-helper/catalog/status` (retained):

```json
{ "v": 1, "on": true, "available": true, "reason": null, "storefront": "us" }
```

`available` is true when `on`, a phone is the source (or reachable), its app has the `catalog` feature, and its `music_status.canPlayCatalog` is true. Otherwise `reason` is one of `off`, `no_phone` (no app phone is the source), `app_update` (the app is too old), `no_subscription`. The cabinet shows Apple Music only when `on`; when `on` but not `available`, the Apple Music screen says why ("Connect your phone", "Update the Juk3box Helper app", "This phone has no Apple Music subscription").

### Asking for lists

`jukebox/jb-source-helper/cmd`:

| action | fields | answers on |
| --- | --- | --- |
| `catalog` | `"what": "genres"` | `…/catalog/genres` |
| `catalog` | `"what": "charts", "genre": "21"` or null | `…/catalog/charts/<genre id, or "all">` |
| `catalog` | `"what": "items", "kind": "playlist"/"album", "id": "…"` | `…/catalog/items/<kind>/<id>` |

| `catalog` | `"what": "all"` | the admin's Refresh now: `…/catalog/genres` and `…/catalog/charts/all` fetched again at once; every other kept list is fetched again the next time it's asked for |

Add `"refresh": true` to ignore the kept copy. A request is answered from the kept copy when it's younger than `refresh_hours` (published again at once, so a screen that just subscribed gets it), otherwise from the app. While fetching, the topic is published with `"state": "loading"` (keeping the old lists if there were any); on failure, `"state": "error"` and `"reason"`.

Every answer is **retained** and carries `v`, `state` (`ready`, `loading`, `error`), `reason`, `at` (epoch seconds of the fetch):

```json
…/catalog/genres:        { "v": 1, "state": "ready", "at": 1794700000, "storefront": "us",
                           "genres": [ { "id": "21", "name": "Rock", "cover": "/covers/<sha1>.jpg", "playlist": <Playlist'> } ],
                           "all": [ { "id": "21", "name": "Rock", "cover": "/covers/<sha1>.jpg" } ] }
…/catalog/charts/<g>:    { "v": 1, "state": "ready", "at": …, "genre": { "id": "21", "name": "Rock" } or null,
                           "playlists": [<Playlist'>], "albums": [<Album'>], "songs": [<Track>] }
…/catalog/items/<k>/<id>: { "v": 1, "state": "ready", "at": …, "kind": "playlist", "id": "pl.…", "name": "Rock Hits",
                           "artist": null, "cover": "/covers/<sha1>.jpg", "songs": [<Track>] }
```

- Genres are filtered and ordered by the `genres` setting (unknown ids are skipped). `all` is every genre in Apple's order, unfiltered, for the admin's Genres list.
- `reason` on an error: the app's (`no_subscription`, `not_authorized`, `offline`, `other`), `no_answer` (the app didn't answer or wake), or the status's (`off`, `no_phone`, `app_update`) when Apple Music isn't available; nothing is sent to the app then. A list with nothing kept yet carries only `v`, `state`, `reason`, `at` and what names it (`genre`, or `kind` and `id`). With `clean` on, explicit albums and songs are dropped from every answer.
- `Playlist'` = `{ "id", "name", "curator", "cover" }`, `Album'` = `{ "id", "name", "artist", "explicit", "cover" }`.
- `cover` is the artwork, **downloaded by jb-source-helper** into its covers folder (`/var/lib/juk3-box/helper/artwork/`) as `<sha1 hex of the artworkUrl>.jpg` (600 px, at most 2 MB, `https` and Apple's image hosts only, `*.mzstatic.com`), so jb-ui serves it at `/covers/<sha1>.jpg` like the app's own uploads. Null until downloaded or when there's none. Answers are published once the covers are in (they download in parallel, at most 8 at once); a cover that fails is null.
- `Track` is a song the screen can pick, in jb-library's track shape plus two fields:

```json
{ "title": "Uprising", "artist": "Muse", "album": "The Resistance", "genre": "Alternative",
  "artwork_url": "/covers/<sha1>.jpg", "item_path": "helper:<device id>/catalog:1440833098",
  "duration_ms": 304800, "explicit": false, "catalog": true }
```

The kept copies live in `/var/lib/juk3-box/helper/catalog/` (one JSON file per topic). A catalog cover is kept as long as some kept list names it; covers no list names are deleted when the lists are refreshed (only covers jb-source-helper downloaded for Apple Music, listed in `catalog/covers.json`; the app's own uploads share the folder and are never touched). A playlist's or album's kept copy not fetched again for 30 days (nobody opened it) is dropped then too. Each phone's `features` and last `canPlayCatalog` are kept in `catalog/phones.json`, so a phone asleep in a pocket still counts as available.

### Playing

A catalog `Track` goes to jb-core like any other (`jukebox/jb-core/cmd` `select` with the track): its `item_path` starts `helper:`, so jb-core sends it to jb-source-helper, which sends the app `play` with `itemId: "catalog:<id>"`. An Apple Music song is the same on every phone, so it plays on the app phone that has the jukebox even when the `<device id>` in its path is another (a kept list another phone fetched); on an app without the `catalog` feature it fails at once with `play-error` reason `not_playable`. jb-source-helper remembers the tracks it has published (by `item_path`), and while a catalog song plays, its `now-playing` carries `artwork_url`, `album` and `catalog: true` from that track, so Now Playing shows its cover.

Recents and Most Played: jb-library records a catalog song that played for 30 s like a library song (it stores the track jb-core sent: title, artist, album, artwork_url, item_path), so it shows in Recents and Most Played. It never joins the library's song list, New, or Shuffle: those songs are published apart from `jukebox/jb-library/tracks`, retained on `jukebox/jb-library/catalog-plays` (`{ "v": 1, "tracks": [<Track> + play_count, last_played_at] }`, newest first, at most 200), and the screen adds them to Recents and Most Played. jb-library counts them from jb-source-helper's `now-playing` (which carries the picked Track's fields), so jb-core is unchanged. The same song from either phone is one song (counted by its Apple id).

## 3. The cabinet (the kiosk engine and themes)

Everything below shows only while `settings.apple_music.on` is true.

| Piece | What it is | Click |
| --- | --- | --- |
| **The Apple Music screen** | layout `apple-music.json`: a header (title "Apple Music", the Apple Music badge, Back), a **Top Playlists** row (the overall chart's playlists, ranked), then a **Genres** grid (every genre's tile: its cover and its name) | A playlist: its songs on the song board. A genre: the genre screen |
| **The genre screen** | layout `apple-music-genre.json`: header (the genre's name, "Apple Music · Genre", Back to the Apple Music screen), rows **Top Playlists**, **Top Albums**, **Top Songs** (song cards) | A playlist or album: its songs on the song board. A song: picks it (plays now if nothing is playing, else joins the queue) |
| **Song board** | a catalog playlist's or album's songs fill the board like a library playlist (header "Apple Music · Rock Hits"); the keypad and the knob pick from it as usual | Picks the song |
| **Nav button** `apple-music` | in the navigation bar, set in Menus › Navigation | Opens the Apple Music screen |
| **Browse sort** `apple-music` | a Browse section card (sorts list, e.g. `"artist,album,genre,apple-music"`), label "Apple Music", subtitle "Genres and charts", a cover from the genres | Opens the Apple Music screen |
| **Section type** `apple-music` | a Home row of genre tiles (`limit`, default 12) with See All | A tile: the genre screen. See All: the Apple Music screen |
| **Block** `apple-music` | a page-builder block. Settings: `show` (`genres`, `top-playlists`, `genre`), `which` (a genre id, for `genre`: that genre's top playlists), `limit`, `title`. Shapes: `tile` (under 0.42 × screen height: one card that opens the Apple Music screen), `row` (a strip of cards, as many as fit), `grid` (from 1.15: a grid) | As the cards above |

- A screen that opens asks for its lists (`catalog` cmd) and shows the kept copy at once if there is one, a loading state otherwise, and the reason when Apple Music isn't available.
- Every card and tile is a knob stop, like Home's cards.
- The engine has working default markup and CSS for all of these (`engine.css`); Neon styles them in its own look (glass cards, coral knob glow, the Apple Music badge `#fa2d48`).

## 4. The admin

- **Sources › Apple Music** (`#/sources/apple-music`): Status (the phone, subscription, storefront, or why it isn't available); **Apple Music on the jukebox** (switch → `settings.apple_music.on`); **Where it shows**: Navigation button (Added ✓ + "Edit in Menus" when `apple-music` is in the nav list, else "Add to navigation", which adds it to the end of the nav list), Card in the Browse row ("Add to Browse": appends `apple-music` to the Home layout's first `browse` section's `sorts`), A Home row of its own ("Add a Home row": appends `{ "block": "section", "type": "apple-music" }` to the Home layout's `home-sections`, after the Browse section), Apple Music block ("Open the Page builder"); **Content**: Clean versions only, Refresh lists every (6 hours, 24 hours, Week) and Refresh now, **Genres** (tick to show, drag to order, each with its tile; written to `genres`); **The Apple Music screen**: "Open in the Page builder" (`#/builder/apple-music`).
- Sources' overview lists Apple Music beside Bluetooth, App and Drives, with its state.
- The page builder offers the `apple-music` block, section type and browse sort, and the two new screens (`apple-music`, `apple-music-genre`) in its screen picker.
