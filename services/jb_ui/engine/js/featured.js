// Featured blocks (a section of type "featured-artist"; "Featured" in the
// admin). Each shows one artist, album, genre or playlist, picked by its
// settings (docs/theme-format.md, "Home sections"):
//
//   feature  artist | album | genre | playlist            (artist)
//   from     any | most-played | recently-added | recently-played | pinned  (any)
//   pin      the one to show when from is "pinned"
//   every    minutes between changes; 0 = never            (30)
//   repeats  true lets one come back before others have had a turn (false)
//
// Picks are kept in the browser (localStorage), so a reload -- a publish,
// a theme change, a restart -- shows the same one until its time is up.
// Several Featured blocks on a page each keep their own pick, and never
// show the same thing at once.

import { store } from './store.js';
import { playlistSongs } from './board.js';

export const KINDS = {
  artist: { label: 'Artist', field: 'artist' },
  album: { label: 'Album', field: 'album' },
  genre: { label: 'Genre', field: 'genre' },
  playlist: { label: 'Playlist', field: 'playlist' },
};
const HEADINGS = {
  any: k => `Featured ${k}`,
  pinned: k => `Featured ${k}`,
  'most-played': k => `Most Played ${k}`,
  'recently-added': k => `New ${k}`,
  'recently-played': k => `Recently Played ${k}`,
};
const RANKED_POOL = 12;     // ranked sources rotate through their top few
const HISTORY = 10;         // no repeats until this many others had a turn
const STORAGE = 'jb-featured';

export function settingsOf(node) {
  const feature = KINDS[node.feature] ? node.feature : 'artist';
  const from = HEADINGS[node.from] ? node.from : 'any';
  const every = node.every === undefined || node.every === '' ? 30 : Number(node.every);
  return {
    feature, from, pin: String(node.pin || ''),
    every: Number.isFinite(every) && every >= 0 ? every : 30,
    repeats: node.repeats === true || node.repeats === 'true',
  };
}

export function headingOf(node) {
  const s = settingsOf(node);
  return HEADINGS[s.from](KINDS[s.feature].label);
}

// The things a block can feature, best first: [{value, cover, songs}].
function pool(tracks, s) {
  const groups = new Map();
  const add = (value, t) => {
    if (!value) return;
    let g = groups.get(value);
    if (!g) groups.set(value, g = { value, songs: [], plays: 0, added: 0, played: 0, recent: Infinity });
    g.songs.push(t);
    g.plays += t.play_count || 0;
    g.added = Math.max(g.added, t.added_at || 0);
    g.played = Math.max(g.played, t.last_played_at || 0);
    // The phone's Albums > Recently Added place (0 = newest), for songs
    // from before the jukebox stamped added_at.
    if (Number.isFinite(t.album_rank)) g.recent = Math.min(g.recent, t.album_rank);
  };
  if (s.feature === 'playlist') {
    for (const p of store.get('playlists') || []) for (const t of playlistSongs(tracks, p.name)) add(p.name, t);
  } else {
    for (const t of tracks) add(t[KINDS[s.feature].field], t);
  }
  let list = [...groups.values()];
  // A card needs a picture: the most played song's cover.
  for (const g of list) {
    const withArt = g.songs.filter(t => t.artwork_url);
    g.cover = withArt.sort((a, b) => (b.play_count || 0) - (a.play_count || 0))[0]?.artwork_url || '';
  }
  list = list.filter(g => g.cover);
  const rank = (key) => list.filter(g => g[key] > 0).sort((a, b) => b[key] - a[key]).slice(0, RANKED_POOL);
  switch (s.from) {
    case 'pinned': return list.filter(g => g.value === s.pin);
    case 'most-played': return rank('plays');
    case 'recently-added':
      // Stamped arrivals first (newest first), then the phone's Recently
      // Added order.
      return list.filter(g => g.added > 0 || g.recent < Infinity)
        .sort((a, b) => b.added - a.added || a.recent - b.recent).slice(0, RANKED_POOL);
    case 'recently-played': return rank('played');
    default: return list;
  }
}

function load() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE)) || {};
  } catch {
    return {};
  }
}

function keep(all) {
  try {
    localStorage.setItem(STORAGE, JSON.stringify(all));
  } catch { /* the pick just won't survive a reload */ }
}

// The block's own name among the page's Featured blocks: its settings plus
// how many identical blocks come before it.
function slotKey(el, s) {
  const id = [s.feature, s.from, s.pin].join('|');
  el.dataset.featuredSlot = id;
  const same = [...document.querySelectorAll('[data-featured-slot]')].filter(x => x.dataset.featuredSlot === id);
  return `${id}#${Math.max(0, same.indexOf(el))}`;
}

// Blocks on the page now: slot -> {el, value, until}. What other blocks
// show is skipped, and the ticker knows when each pick runs out.
const live = new Map();

// What this block shows now: {value, cover, songs} or null.
export function featuredFor(el, node, tracks) {
  if (!el.isConnected) return null;    // its place on the page decides its slot
  for (const [k, v] of live) if (!v.el.isConnected) live.delete(k);
  const s = settingsOf(node);
  const list = pool(tracks, s);
  if (!list.length) return null;
  const all = load();
  const key = slotKey(el, s);
  const now = Date.now();
  const mine = all[key];
  const current = mine && list.find(g => g.value === mine.value);
  let pick;
  if (s.every === 0 && s.from !== 'any') {
    pick = list[0];                       // pinned, or the top of a ranked list
  } else if (current && (s.every === 0 || now < mine.until)) {
    pick = current;                       // still its turn
  } else {
    const shown = new Set([...live].filter(([k]) => k !== key && k.startsWith(`${s.feature}|`)).map(([, v]) => v.value));
    const history = s.repeats ? [] : (mine?.history || []);
    let choices = list.filter(g => !shown.has(g.value));
    if (!choices.length) choices = list;
    const fresh = choices.filter(g => !history.includes(g.value) && g.value !== mine?.value);
    if (fresh.length) choices = fresh;
    pick = choices[Math.floor(Math.random() * choices.length)];
  }
  const until = s.every === 0 ? Number.MAX_SAFE_INTEGER : (pick === current ? mine.until : now + s.every * 60000);
  if (pick !== current || !mine) {
    const history = mine?.history || [];
    all[key] = { value: pick.value, until, history: [pick.value, ...history].slice(0, Math.min(HISTORY, Math.floor(list.length / 2))) };
    keep(all);
  }
  live.set(key, { el, value: pick.value, until });
  return pick;
}

// A tick when any pick runs out, so its block picks again. Also every 30
// minutes regardless, for things that rotate with it (Browse card covers).
export function startFeatured() {
  let lastHalfHour = Math.floor(Date.now() / 1800000);
  setInterval(() => {
    const now = Date.now();
    const half = Math.floor(now / 1800000);
    if ([...live.values()].some(v => v.until <= now) || half !== lastHalfHour) {
      lastHalfHour = half;
      store.set('featured', now);
    }
  }, 15000);
}
