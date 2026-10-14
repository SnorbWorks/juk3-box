# Contract: configuration

Version 1 · **Draft, proven against the reference build** · Oct 13, 2026

Anything an owner or builder would want to change lives in data a tool can edit, never in a module's source. This is the shape of that data.

## Three layers

| Layer | Who changes it | Where | Example |
| --- | --- | --- | --- |
| **Module config** | The builder (installer, SSH, later the admin) | `/etc/juk3-box/<module>.json` | MQTT host, audio device, Last.fm key |
| **Owner settings** | The owner, from the admin or the cabinet's Settings screen | `/var/lib/juk3-box/settings.json` | Active theme, nav buttons, songs per page |
| **Owner layouts** | The owner, from the page builder | `/var/lib/juk3-box/layouts/<theme>/<layout>.json` | Their own Home screen |
| **Owner styles** | The owner, from the admin's Appearance page | `/var/lib/juk3-box/styles/<theme>/custom.json` | Sunset colors with a red button |

Defaults live with the code that uses them, so a missing or partly broken file always falls back to something that works. A module never refuses to start because of an owner setting.

## Owner settings: `settings.json`

One JSON object. Every key is optional; anything missing or unusable means "the default".

| Key | Type | Default | Used by |
| --- | --- | --- | --- |
| `theme` | theme id | `bare` | jb-ui |
| `nav` | list of nav button names, in order | `home, volume, shuffle, all-songs, settings` | jb-ui |
| `songs_per_page` | 10..100, step 10 | 100 | jb-ui |
| `idle_home_minutes` | number ≥ 0 (0 = never) | 5 | jb-ui |

Unknown keys are kept as they are when a tool saves the file, so settings added by a newer module survive an older admin.

The admin describes each setting with a schema (type, range, label, help) so it can draw a form without knowing the module. For the reference build that schema is `services/jb_admin/schema.py`; it moves into each module's `module.json` (`"owner_settings": {...}`) when a second module needs owner settings.

## Owner layouts

The theme's layouts (`<theme>/layouts/<name>.json`) are its defaults and are never edited. The owner's version of a layout is saved per theme, so switching themes and back keeps each one's edits:

```
/var/lib/juk3-box/layouts/<theme-id>/<layout>.json      published: the cabinet shows these
/var/lib/juk3-box/drafts/<theme-id>/<layout>.json       drafts: only the admin preview shows these
```

- **Publish** moves the draft over the published layout. **Reset to theme default** deletes the published layout (and the draft).
- jb-ui validates an owner layout exactly like a theme's (see [theme-format.md](../theme-format.md), Layouts). An owner layout that fails is ignored, with a warning in the log, and the theme's own layout is shown. A bad edit can never blank the cabinet.

## Owner styles

A theme's global options (see [theme-format.md](../theme-format.md), Customize) with the owner's choices, per theme:

```
/var/lib/juk3-box/styles/<theme-id>/custom.json          published: {"style": "sunset", "values": {"button": "#e0303a"}}
/var/lib/juk3-box/style-drafts/<theme-id>/custom.json    draft: only the admin preview shows it
/var/lib/juk3-box/style-revisions/<theme-id>/custom/     every published version, the newest 20
```

`values` holds only what differs from the style. jb-ui lays them over the theme's defaults and the style; an unusable value is skipped with a warning, never a broken screen.

## Writing

- Every write is atomic: write a temporary file in the same folder, then rename it over the old one. A reader sees the old file or the new one, never half of one.
- One writer per file: settings and owner layouts are written only by jb-admin (and later by jb-ui's Settings screen through a jb-admin command, so there is still one writer).
- After a change the writer publishes the event `jukebox/jb-admin/changed`: `{"what": ["settings" | "layouts" | "customize" | "theme"], "at": <unix seconds>}`. It is not retained, so a module starting later just reads the files. jb-ui reloads its page on it, which is how a published edit reaches the cabinet.

## Proving notes

- jb-ui already reads `settings.json` this way (`owner_settings()` in `services/jb_ui/main.py`): every key validated, defaults on anything odd.
- The owner-layout overlay is the gap this version closes: the theme format promised it; jb-ui now implements it.
- Module config (`/etc/juk3-box/`) is root-owned and edited over SSH. Bringing it into the admin waits for the per-module settings schema (above), so it isn't designed against one module.
