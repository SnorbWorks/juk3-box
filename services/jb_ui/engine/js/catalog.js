// Apple Music, the catalog (docs/contracts/apple-music.md, section 3): its
// genres, the charts (top playlists, albums and songs, overall or for one
// genre) and a chart playlist's or album's songs. jb-source-helper fetches
// them through the Juk3box Helper app and publishes each list, retained, on
// its own topic; this module asks for them, keeps track of which topics are
// watched, and turns them into cards. Nothing here draws: the blocks
// (applemusic.js), the Home row and Browse card (sections.js) and the song
// board (board.js) use it.
//
// Everything Apple Music hides while the owner's switch
// (settings.apple_music.on) is off.

import { publish, unwatchTopic, watchTopic } from './bus.js';
import { store } from './store.js';

const HELPER = 'jukebox/jb-source-helper';
export const CATALOG_TOPIC = `${HELPER}/catalog`;
// Bumped whenever any chart or items topic changes, so whatever shows one
// redraws (each compares what it shows and leaves unchanged cards be).
export const TICK = 'catalog';

// A list is asked for again at most this often: every screen that opens
// asks, and jb-source-helper answers from its kept copy while it's fresh.
const ASK_AGAIN_MS = 30000;
// Chart and items topics watched at once. Each is a retained list of up to
// 300 songs; the oldest are let go (and asked for again if they're needed).
const WATCH_MAX = 10;

export const CHARTS_ALL = 'all';
export const chartsTopic = genre => `${CATALOG_TOPIC}/charts/${genre || CHARTS_ALL}`;
export const itemsTopic = (kind, id) => `${CATALOG_TOPIC}/items/${kind}/${id}`;

// Why Apple Music can't be browsed (catalog/status reason), as the cabinet
// says it.
export const REASONS = {
  no_phone: { message: 'Connect your phone', detail: 'Apple Music plays through the Juk3box Helper app on a connected phone.' },
  app_update: { message: 'Update the Juk3box Helper app', detail: 'This version of the app can\'t browse Apple Music yet.' },
  no_subscription: { message: 'This phone has no Apple Music subscription', detail: 'Songs from your library still play as usual.' },
};
// Why a list couldn't be fetched (state "error").
const LIST_ERRORS = {
  no_subscription: 'This phone has no Apple Music subscription',
  not_authorized: 'Allow Apple Music in the Juk3box Helper app',
  offline: 'Apple Music can\'t be reached right now',
};

// The owner's switch: off hides every Apple Music entry point.
export function appleMusicOn() {
  return !!store.get('helperSettings')?.apple_music?.on;
}

// Whether lists can be fetched and songs played now:
// { available, reason, message, detail }. Before jb-source-helper has said
// (no status yet), it counts as loading, not as unavailable.
export function availability() {
  const s = store.get('catalogStatus');
  if (!s) return { available: false, reason: 'loading', message: 'Loading…', detail: '', loading: true };
  if (s.available) return { available: true, reason: null, message: '', detail: '' };
  const why = REASONS[s.reason] || { message: 'Apple Music isn\'t available right now', detail: '' };
  return { available: false, reason: s.reason || 'other', ...why };
}

// A list topic's payload, or null.
export const genresData = () => store.get('catalogGenres') || null;
export const chartsData = genre => store.get(`topic:${chartsTopic(genre)}`) || null;
export const itemsData = (kind, id) => store.get(`topic:${itemsTopic(kind, id)}`) || null;

// The genres to show (jb-source-helper already filtered and ordered them by
// the owner's choice): [{id, name, cover, playlist}].
export function genres() {
  return (genresData()?.genres || []).filter(g => g && g.id && g.name);
}

export function genreById(id) {
  return genres().find(g => String(g.id) === String(id)) || null;
}

// Watching and asking ----------------------------------------------------

const watched = [];   // chart and items topics, least recently needed first

// Subscribe to a list's topic (its retained copy arrives at once), letting
// the least recently needed go beyond WATCH_MAX. Genres and status are
// always subscribed (bus.js).
export function watchList(topic) {
  const i = watched.indexOf(topic);
  if (i >= 0) watched.splice(i, 1);
  watched.push(topic);
  watchTopic(topic, TICK);
  while (watched.length > WATCH_MAX) unwatchTopic(watched.shift());
}

const asked = new Map();   // request -> when it was last asked

// Ask jb-source-helper for a list: { what: "genres" }, { what: "charts",
// genre }, { what: "items", kind, id }. Only while Apple Music is on and
// available (it would only answer with an error otherwise), and not again
// within ASK_AGAIN_MS.
export function ask(request, { force = false } = {}) {
  if (!appleMusicOn() || !availability().available) return false;
  const key = JSON.stringify(request);
  const now = Date.now();
  if (!force && now - (asked.get(key) || 0) < ASK_AGAIN_MS) return false;
  asked.set(key, now);
  publish(`${HELPER}/cmd`, { action: 'catalog', ...request });
  return true;
}

// Apple Music just became available (a phone connected): what was asked
// before it was may be asked again at once.
let wasAvailable = false;
export function statusChanged() {
  const now = availability().available;
  if (now && !wasAvailable) asked.clear();
  wasAvailable = now;
}

// Everything one view needs: watch its topic and ask for it.
export function request(what, { genre = null, kind, id } = {}) {
  if (!appleMusicOn()) return false;
  if (what === 'genres') return ask({ what: 'genres' });
  if (what === 'charts') {
    watchList(chartsTopic(genre));
    return ask({ what: 'charts', genre: genre || null });
  }
  watchList(itemsTopic(kind, id));
  return ask({ what: 'items', kind, id });
}

// Cards --------------------------------------------------------------------

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// What a list's state says when it has nothing to show yet.
export function listMessage(data) {
  if (!data || data.state === 'loading') return { message: 'Loading…', loading: true };
  if (data.state === 'error') return { message: LIST_ERRORS[data.reason] || 'Couldn\'t load this from Apple Music', loading: false };
  return { message: 'Nothing here right now', loading: false };
}

// A genre's tile: its #1 chart playlist's cover, with its name over it.
export function genreCard(g) {
  return { kind: 'genre', id: String(g.id), label: g.name, image: g.cover || g.playlist?.cover || '', genre: { id: String(g.id), name: g.name } };
}

// A chart playlist or album: its cover, name and who made it; ranked when
// it's a chart's (#1, #2...).
export function listCard(item, kind, rank) {
  return {
    kind,
    id: String(item.id),
    label: item.name || '',
    description: (kind === 'album' ? item.artist : item.curator) || '',
    image: item.cover || '',
    explicit: !!item.explicit,
    rank: rank || 0,
    rankLabel: rank ? `#${rank}` : '',
  };
}

// A chart song: a track jb-core can play, with its place in the chart.
export function songCard(track, rank) {
  return { kind: 'song', track, id: track.item_path || track.title, rank: rank || 0, rankLabel: rank ? String(rank) : '' };
}

// The cards a chart gives for one kind of list.
export function chartCards(data, list) {
  if (!data) return [];
  // Albums aren't numbered (the design): playlists and songs are.
  if (list === 'albums') return (data.albums || []).filter(a => a?.id).map(a => listCard(a, 'album', 0));
  if (list === 'songs') return (data.songs || []).filter(t => t?.title).map((t, i) => songCard(t, i + 1));
  return (data.playlists || []).filter(p => p?.id).map((p, i) => listCard(p, 'playlist', i + 1));
}

// Two covers for the Apple Music card (the Browse card, the block's tile
// shape): from the genres' tiles, the same two all through each half hour.
export function cardCovers(pick) {
  const covers = [...new Set(genres().map(g => g.cover || g.playlist?.cover).filter(Boolean))];
  const first = pick(covers, 'apple-music:1') || '';
  const second = pick(covers.filter(c => c !== first), 'apple-music:2') || '';
  return [first, second];
}

// What the block shows (the apple-music block, docs/theme-format.md):
//   show   genres | top-playlists | top-albums | top-songs | genre
//   which  a genre id; empty: the overall charts, or on the genre screen
//          the genre it shows
//   shape  tile (one card that opens the Apple Music screen) | row | grid
// Returns { hidden } while Apple Music is off; { tile } for the tile shape;
// else { title, cards, message, detail, loading, unavailable }.
export const SHOWS = ['genres', 'top-playlists', 'top-albums', 'top-songs', 'genre'];
const TITLES = { genres: 'Genres', 'top-playlists': 'Top Playlists', 'top-albums': 'Top Albums', 'top-songs': 'Top Songs', genre: 'Top Playlists' };
const LISTS = { 'top-playlists': 'playlists', 'top-albums': 'albums', 'top-songs': 'songs', genre: 'playlists' };

export function settingsOf(node, screenGenre = null) {
  const show = SHOWS.includes(node.show) ? node.show : 'genres';
  const which = String(node.which ?? '').trim();
  const limit = Number(node.limit) > 0 ? Math.floor(Number(node.limit)) : 0;
  // The genre a chart is for: the one named, else the genre screen's own.
  const genre = show === 'genres' ? null : (which || screenGenre?.id || null);
  return { show, which, limit, genre, title: typeof node.title === 'string' ? node.title : '' };
}

export function blockView(node, { shape = 'row', screenGenre = null } = {}) {
  if (!appleMusicOn()) return { hidden: true };
  if (shape === 'tile') return { tile: true };
  const s = settingsOf(node, screenGenre);
  const genreName = s.genre ? (genreById(s.genre)?.name || (screenGenre?.id === s.genre ? screenGenre.name : '')) : '';
  let title = s.title || TITLES[s.show];
  if (!s.title && s.show === 'genre' && genreName) title = `${genreName} Playlists`;
  const status = availability();
  if (!status.available) {
    return { title, cards: [], message: status.message, detail: status.detail, loading: !!status.loading, unavailable: true };
  }
  const data = s.show === 'genres' ? genresData() : chartsData(s.genre);
  let cards = s.show === 'genres' ? genres().map(genreCard) : chartCards(data, LISTS[s.show]);
  if (s.limit) cards = cards.slice(0, s.limit);
  const empty = cards.length ? null : listMessage(data);
  return { title, cards, message: empty?.message || '', detail: '', loading: !!empty?.loading, unavailable: false };
}

// The Home row of genre tiles (section type apple-music): its cards, or
// none while off (the row then hides).
export function homeRowCards(limit = 12) {
  if (!appleMusicOn()) return [];
  return genres().slice(0, limit).map(genreCard);
}

// The song board ------------------------------------------------------------

// A catalog playlist's or album's songs, in order, as tracks jb-core can
// play (their item_path starts "helper:"), or [] until they arrive.
export function catalogSongs(kind, id) {
  const songs = itemsData(kind, id)?.songs;
  return Array.isArray(songs) ? songs.filter(t => t && t.title) : [];
}

// While the board's catalog list has no songs: { loading } or { failed }.
export function catalogStatus(kind, id) {
  const data = itemsData(kind, id);
  if (catalogSongs(kind, id).length) return null;
  if (!availability().available) return { failed: availability().message };
  if (!data || data.state === 'loading') return { loading: true };
  if (data.state === 'error') return { failed: listMessage(data).message };
  return null;
}

// The board header's words for a catalog list: "Apple Music · Rock Hits".
export function catalogTitle(name) {
  return name ? `Apple Music · ${name}` : 'Apple Music';
}

export function catalogSubtitle(kind, id) {
  const d = itemsData(kind, id);
  const n = catalogSongs(kind, id).length;
  return [d?.artist, n ? plural(n, 'song') : ''].filter(Boolean).join(' · ');
}
