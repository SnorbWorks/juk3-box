# Apple Music: Plan

Status: **design**, 2026-11-14. Nothing is built yet.

## The idea

When the owner has an Apple Music subscription, the jukebox can browse Apple Music itself, not just the owner's Library: its genres, each genre's top playlists, albums and songs, all with cover art. Songs picked there play through the Juk3box Helper app like any other song.

The jukebox has no Apple account. Everything Apple Music comes through the **Helper app** on the owner's phone (MusicKit, the owner's subscription). The Pi asks the app for lists, keeps them for a day, and downloads the cover art itself, because Apple's artwork links are public.

## Where it shows up

Apple Music is one feature with **one page of its own** (the Apple Music screen). Every entry point leads there.

| Entry point | Where the owner sets it up | What it does |
|---|---|---|
| **Sources > Apple Music** (new, beside Bluetooth, App and Drives) | Admin | Turns Apple Music on or off, and has the shortcuts below |
| **Navigation button** | Menus > Navigation (Sources has an "Add to navigation" shortcut that adds it there) | Opens the Apple Music screen |
| **A card in the Browse row** | Sources shortcut "Add to Browse", or the Browse section's `sorts` (`apple-music`) | One card that opens the Apple Music screen |
| **A row of its own on Home** | Sources shortcut "Add a Home row", or the page builder | A new section type `apple-music`: a row of genre tiles; See All opens the Apple Music screen |
| **The Apple Music block** | Page builder, anywhere | A block that fits its space (shapes, below) |

Rule kept from the platform: the navigation bar's contents are only set in Menus > Navigation. The Sources shortcut adds it there and links to it.

### Sources > Apple Music

- **Status:** which phone provides it, and whether that phone has a subscription (`music_status.canPlayCatalog`). With no subscribed phone, the page explains what's needed and the switch is off.
- **Apple Music on/off.** Off by default. When it's off, every entry point hides itself, so a theme or layout that includes them still looks right.
- **Shortcuts:** Add to navigation · Add to Browse · Add a Home row. Each shows "Added ✓" with a link to where it lives.
- **Content:**
  - Clean versions only (hide explicit songs). **Off by default** (owner, 2026-11-14): explicit songs show, the same as on the phone.
  - Which genres show, and in what order (all, or pick and drag).
  - Refresh lists every: 24 h (default).

### The Apple Music block (page builder)

| Shape | Shows |
|---|---|
| `tile` (narrow) | One card that opens the Apple Music screen |
| `row` | A strip of genre tiles (or one genre's playlists), as many as fit |
| `grid` | A grid of genre tiles |

Settings: `show` (`genres`, `genre` with `which`, `top-playlists`), `limit`, `title`.

## The Apple Music screen

It's a new screen (layout `apple-music.json`). Themes can style it, and the page builder can rearrange it like Home. **Owner picked (2026-11-14): a charts row on top, then a genre grid.** A visual mockup in Neon comes next.

```
┌ [Apple Music]  Apple Music                                   ◀ Back ┐
│ TOP PLAYLISTS  ▸ [art][art][art][art][art][art]                      │
│ GENRES                                                               │
│ [Pop     ][Rock    ][Hip-Hop ][Country ][R&B     ][Alternative]      │
│ [Dance   ][Jazz    ][Classical][Oldies ][Latin   ][Christian  ]      │
└──────────────────────────────────────────────────────────────────────┘
Genre screen ("Rock"):
┌ [Apple Music] › Rock                                         ◀ Back ┐
│ TOP PLAYLISTS ▸ [art][art][art][art][art]                            │
│ TOP ALBUMS    ▸ [art][art][art][art][art]                            │
│ TOP SONGS     ▸ [song][song][song][song][song]   (picking one plays) │
└──────────────────────────────────────────────────────────────────────┘
Picking a playlist or album opens the song board with its songs (header "Apple Music · Rock Hits").
```

- **Genre tiles:** Apple's genre list has no artwork. Each tile uses the cover of that genre's #1 chart playlist, with the genre name over it. Fallback: a 2×2 grid of its top album covers.
- **The "Apple Music" badge** goes in the header, per Apple's Apple Music identity guidelines.
- **Knob:** every tile and card is a knob stop, the same as Home.

## Playing

- Catalog songs are picked like any other: the song plays now if nothing is playing, otherwise it joins the queue.
- They count in Recents and Most Played, with their Apple artwork.
- They're **not** added to the owner's Library (to be decided: maybe an option).

## What to build

| Part | Work |
|---|---|
| Helper app | New commands: `catalog_genres`; `catalog_charts {genre?}` (playlists, albums, songs); `catalog_items {playlist or album id}`; `play` accepts a catalog song id. Uses MusicKit (`MusicCatalogChartsRequest`, `MusicDataRequest` for `/v1/catalog/{storefront}/genres`). **Needs an App Store review**, after 1.0 and the queued Forget/Hide update. |
| jb-source-helper | Sends those commands, caches the answers (24 h), downloads the artwork, publishes `jukebox/jb-source-helper/catalog/...`, and plays catalog ids. |
| jb-ui engine | The `apple-music` screen and its genre screen; the section type; the browse sort; the nav button; the block with shapes; board support for a catalog playlist or album. |
| jb-admin | The Sources > Apple Music page and its shortcuts; content settings; block schema entries. |
| Themes | Neon styles for the new screen and block; `bare` gets the engine defaults. |

## Order

1. **Spike:** the Helper app fetches the genres and each one's #1 playlist cover, and the admin shows them. That proves the art looks right.
2. Protocol plus jb-source-helper (lists, cache, art, catalog play).
3. The Apple Music screen and the genre screen in the engine, plus Neon styles.
4. Entry points: nav button, browse card, Home row, block.
5. Sources > Apple Music in the admin.
6. Helper app update to App Store review.

## Open questions

- The Apple Music screen: the visual details in Neon (tile size, how many across, the genre screen).
- Should picked songs be added to the owner's Library?
- When the phone isn't connected: hide Apple Music, or show it greyed out with "Connect your phone"?
