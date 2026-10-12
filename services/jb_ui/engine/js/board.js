// The song board's state: which list it shows (sort, and a drill-down
// filter), which page, and how a slot code maps to a song. The board
// block draws this; the keypad and the pager change it.

import { store } from './store.js';

export const LETTERS = 'ABCDEFGHJK';   // no I
export const DIGITS = '1234567890';

// Sorts the engine knows (docs/theme-format.md, "Home sections").
// Category sorts list one slot per artist/album/genre first; picking one
// shows its songs.
export const SORTS = {
  title: { label: 'Songs' },
  artist: { label: 'Artists', singular: 'Artist', category: true },
  album: { label: 'Albums', singular: 'Album', category: true },
  genre: { label: 'Genres', singular: 'Genre', category: true },
  // The phone's own playlists (jb-crawl's deep crawl, via jb-library).
  playlist: { label: 'Playlists', singular: 'Playlist', category: true },
  'top-100': { label: 'Top 100' },
  'most-played': { label: 'Most Played' },
};

const byTitle = (a, b) => (a.title || '').localeCompare(b.title || '');

// A playlist's songs in the phone's order. Playlists are stored by title
// (play paths don't survive reconnects): each title takes the next unused
// copy of that title, and titles no longer in the library are skipped.
export function playlistSongs(tracks, name) {
  const p = (store.get('playlists') || []).find(pl => pl.name === name);
  if (!p) return [];
  const used = new Set();
  return p.titles.map(title => {
    const t = tracks.find(x => x.title === title && !used.has(x));
    if (t) used.add(t);
    return t;
  }).filter(Boolean);
}

function songsFor(tracks, sort, filter) {
  if (filter?.field === 'playlist') return playlistSongs(tracks, filter.value);
  if (filter?.field === 'album') {
    // Album order where the phone told us the track numbers (deep crawl).
    return tracks.filter(t => t.album === filter.value)
      .sort((a, b) => (a.album_track ?? 1e9) - (b.album_track ?? 1e9) || byTitle(a, b));
  }
  if (filter) return tracks.filter(t => t[filter.field] === filter.value).sort(byTitle);
  if (sort === 'top-100') {
    // Global popularity (Last.fm listeners, from jb-library).
    return tracks.filter(t => t.popularity).sort((a, b) => b.popularity - a.popularity || byTitle(a, b)).slice(0, 100);
  }
  if (sort === 'most-played') {
    // Plays on this jukebox (jb-library counts a play after 30s).
    return tracks.filter(t => t.play_count > 0)
      .sort((a, b) => b.play_count - a.play_count || (b.last_played_at || 0) - (a.last_played_at || 0) || byTitle(a, b));
  }
  return [...tracks].sort(byTitle);
}

function categoriesFor(tracks, field) {
  if (field === 'playlist') return (store.get('playlists') || []).map(p => p.name);   // the phone's order
  const values = [...new Set(tracks.map(t => t[field]).filter(Boolean))];
  return values.sort((a, b) => a.localeCompare(b));
}

let state = { sort: 'title', filter: null, page: 0 };

function digitsPerLetter() {
  return store.get('settings')?.songs_per_page / LETTERS.length || DIGITS.length;
}

// What the board shows now: items for the current page with their codes.
export function view() {
  const tracks = store.get('tracks') || [];
  const isCategory = SORTS[state.sort]?.category && !state.filter;
  const all = isCategory
    ? categoriesFor(tracks, state.sort).map(value => ({ category: value }))
    : songsFor(tracks, state.sort, state.filter);
  const digits = digitsPerLetter();
  const perPage = digits * LETTERS.length;
  const pages = Math.max(1, Math.ceil(all.length / perPage));
  const page = Math.min(state.page, pages - 1);
  const slots = [];
  for (let i = 0; i < perPage; i++) {
    const letter = LETTERS[Math.floor(i / digits)];
    const digit = DIGITS[i % digits];
    slots.push({ code: letter + digit, letter, item: all[page * perPage + i] || null });
  }
  return {
    sort: state.sort,
    sortLabel: SORTS[state.sort]?.label || state.sort,
    filter: state.filter,
    page,
    pages,
    digits,
    isCategory,
    slots,
  };
}

function changed() {
  store.set('board', { ...state });
}

export function setSort(sort) {
  if (!SORTS[sort]) return;
  state = { sort, filter: null, page: 0 };
  changed();
}

export function turnPage(dir) {
  const { page, pages } = view();
  state.page = (((page + dir) % pages) + pages) % pages;
  changed();
}

// Back up from an artist's (album's, genre's) songs to the list of them.
// Returns whether there was a level to go back up from.
export function upLevel() {
  if (!state.filter) return false;
  state = { sort: state.sort, filter: null, page: state.categoryPage || 0 };
  changed();
  return true;
}

// The keypad picked slot `index` (A1 = 0 ... K0 = 99, jb-core's order).
// Returns the song to queue, or null (empty slot, or a category opened).
export function pick(index) {
  const letter = Math.floor(index / DIGITS.length);
  const digit = index % DIGITS.length;
  const digits = digitsPerLetter();
  if (digit >= digits) return null;  // e.g. A7 with 5 songs per letter
  const slot = view().slots[letter * digits + digit];
  if (!slot?.item) return null;
  if (slot.item.category !== undefined) {
    state = { sort: state.sort, filter: { field: state.sort, value: slot.item.category }, page: 0, categoryPage: state.page };
    changed();
    return null;
  }
  return slot.item;
}

// Straight to one artist's (album's, genre's) songs, e.g. from the
// Featured Artist card. Backing up lands on the full list of them.
export function openFilter(field, value) {
  if (!SORTS[field]?.category) return;
  state = { sort: field, filter: { field, value }, page: 0, categoryPage: 0 };
  changed();
}

// Cover art found among a sort's songs (for Browse cards without a
// picture of their own).
export function coversFor(sort) {
  const tracks = store.get('tracks') || [];
  const pool = SORTS[sort]?.category ? tracks : songsFor(tracks, sort, null);
  return [...new Set(pool.map(t => t.artwork_url).filter(Boolean))];
}
