import { store } from './store.js';
// Turns service payloads into the track fields themes use
// (docs/theme-format.md, "Data fields").

export function formatMs(ms) {
  if (typeof ms !== 'number' || ms < 0) return '';
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// The phone only gives titles; covers and the like come from jb-library's
// lookups. Index the library by title so anything with just a title can
// be filled in. (Duplicate titles share the first match.)
export function libraryIndex(tracks) {
  const byTitle = new Map();
  for (const t of tracks) {
    if (t.title && !byTitle.has(t.title)) byTitle.set(t.title, t);
  }
  return byTitle;
}

export function toTrack(raw, library) {
  const known = (library && raw.title && library.get(raw.title)) || {};
  return {
    title: raw.title || '',
    artist: raw.artist || known.artist || '',
    album: raw.album || known.album || '',
    year: raw.year || known.year || '',
    length: formatMs(raw.duration_ms ?? known.duration_ms),
    cover: raw.artwork_url || known.artwork_url || '',
  };
}

// Apple's artwork URLs end in a size ("…/600x600bb.jpg") and the server
// scales to whatever size is asked for. Small thumbnails keep a full board
// of covers within the Pi's memory.
export function sized(url, px) {
  return url ? url.replace(/\/\d+x\d+bb\./, `/${px}x${px}bb.`) : url;
}

// One of a list, the same for the same key all through each half hour --
// across re-renders and reloads -- and a different one the next. For
// pictures that change on their own (Browse and Shortcut covers).
export function steadyPick(list, key) {
  if (!list.length) return undefined;
  const text = `${key}|${Math.floor(Date.now() / 1800000)}`;
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return list[(h >>> 0) % list.length];
}

// The library plus the Apple Music songs played here, for the views that
// rank by plays (Recents, Most Played). Nothing else shows catalog songs.
export function withCatalogPlays(tracks) {
  const played = store.get('catalogPlays') || [];
  return played.length ? [...tracks, ...played] : tracks;
}
