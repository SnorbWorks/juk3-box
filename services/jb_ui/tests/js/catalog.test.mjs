// The engine's Apple Music data (engine/js/catalog.js) and the song board
// filled from a catalog list (engine/js/board.js), with a fake bus: the
// payloads are the contract's (docs/contracts/apple-music.md, section 2).
// Run: node --test services/jb_ui/tests/js/  (test_engine_js.py runs it too)

import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// The engine runs in a browser; these are the few globals its data modules
// touch. The bus is mqtt.js's client, faked: what's published is recorded,
// and messages are delivered as the broker would.
globalThis.location = { search: '', hostname: 'localhost', protocol: 'http:', host: 'localhost' };
const published = [];
const subscribed = new Set();
const handlers = {};
const client = {
  connected: true,
  on(event, fn) { handlers[event] = fn; },
  subscribe(t) { for (const x of [].concat(t)) subscribed.add(x); },
  unsubscribe(t) { subscribed.delete(t); },
  publish(topic, payload) { published.push({ topic, payload: JSON.parse(payload) }); },
};
globalThis.mqtt = { connect: () => client };

const engine = new URL('../../engine/js/', import.meta.url);
const { connectBus } = await import(new URL('bus.js', engine));
const { store } = await import(new URL('store.js', engine));
const catalog = await import(new URL('catalog.js', engine));
const board = await import(new URL('board.js', engine));

connectBus();
handlers.connect();
const deliver = (topic, data) => handlers.message(topic, Buffer.from(JSON.stringify(data)));

const H = 'jukebox/jb-source-helper';
const cover = n => `/covers/${String(n).padStart(40, '0')}.jpg`;
const track = (n, extra = {}) => ({
  title: `Song ${n}`, artist: `Artist ${n}`, album: 'Album', genre: 'Rock', artwork_url: cover(n),
  item_path: `helper:dev1/catalog:${1000 + n}`, duration_ms: 200000, explicit: false, catalog: true, ...extra,
});
const playlist = n => ({ id: `pl.${n}`, name: `Playlist ${n}`, curator: 'Apple Music Rock', cover: cover(n) });
const album = n => ({ id: `${500 + n}`, name: `Album ${n}`, artist: `Artist ${n}`, explicit: false, cover: cover(n) });

function switchOn(on = true) {
  deliver(`${H}/settings`, { apple_music: { on, clean: false, genres: null, refresh_hours: 24 } });
}
function available(yes = true, reason = null) {
  deliver(`${H}/catalog/status`, { v: 1, on: true, available: yes, reason: yes ? null : reason, storefront: 'us' });
}
const genresPayload = {
  v: 1, state: 'ready', at: 1794700000, genres: [
    { id: '21', name: 'Rock', cover: cover(1), playlist: playlist(1) },
    { id: '14', name: 'Pop', cover: null, playlist: playlist(2) },
    { id: '20', name: 'Alternative', cover: cover(3), playlist: null },
  ],
};

beforeEach(() => {
  published.length = 0;
  store.set('settings', { selection: { kind: 'letters', letters: 'AB', digits: '123' } });   // 6 slots a page
});

test('everything hides while Apple Music is off', () => {
  switchOn(false);
  available();
  deliver(`${H}/catalog/genres`, genresPayload);
  assert.equal(catalog.appleMusicOn(), false);
  assert.deepEqual(catalog.blockView({ show: 'genres' }), { hidden: true });
  assert.deepEqual(catalog.blockView({ show: 'genres' }, { shape: 'tile' }), { hidden: true });
  assert.deepEqual(catalog.homeRowCards(), []);
  // And nothing is asked for.
  assert.equal(catalog.request('genres'), false);
  assert.equal(published.length, 0);
});

test('a missing settings message counts as off', () => {
  store.set('helperSettings', undefined);
  assert.equal(catalog.appleMusicOn(), false);
  store.set('helperSettings', {});
  assert.equal(catalog.appleMusicOn(), false);
});

test('when not available, the block says why (and asks for nothing)', () => {
  switchOn();
  for (const [reason, words] of [['no_phone', 'Connect your phone'], ['app_update', 'Update the Juk3box Helper app'],
    ['no_subscription', 'This phone has no Apple Music subscription']]) {
    available(false, reason);
    const v = catalog.blockView({ show: 'genres' });
    assert.equal(v.unavailable, true);
    assert.equal(v.message, words);
    assert.deepEqual(v.cards, []);
  }
  assert.equal(catalog.request('genres'), false);
  assert.equal(published.length, 0);
  // Before jb-source-helper has said anything: loading, not a reason.
  store.set('catalogStatus', undefined);
  const v = catalog.blockView({ show: 'genres' });
  assert.equal(v.loading, true);
  assert.equal(v.message, 'Loading…');
});

test('genre tiles come from the genres topic, in its order', () => {
  switchOn();
  available();
  deliver(`${H}/catalog/genres`, genresPayload);
  const v = catalog.blockView({ show: 'genres' });
  assert.equal(v.title, 'Genres');
  assert.deepEqual(v.cards.map(c => [c.kind, c.id, c.label]), [['genre', '21', 'Rock'], ['genre', '14', 'Pop'], ['genre', '20', 'Alternative']]);
  // A genre with no cover of its own uses its playlist's.
  assert.equal(v.cards[1].image, cover(2));
  assert.deepEqual(v.cards[0].genre, { id: '21', name: 'Rock' });
  assert.equal(catalog.blockView({ show: 'genres', limit: 2 }).cards.length, 2);
  assert.equal(catalog.homeRowCards(1).length, 1);
});

test('asking: the contract command, once in a while, and again once available', () => {
  switchOn();
  available();
  catalog.statusChanged();
  assert.equal(catalog.request('charts', { genre: '21' }), true);
  assert.deepEqual(published.at(-1), { topic: `${H}/cmd`, payload: { action: 'catalog', what: 'charts', genre: '21' } });
  assert.ok(subscribed.has(`${H}/catalog/charts/21`));
  // Asked a moment ago: not again.
  assert.equal(catalog.request('charts', { genre: '21' }), false);
  assert.equal(catalog.request('charts'), true);
  assert.deepEqual(published.at(-1).payload, { action: 'catalog', what: 'charts', genre: null });
  assert.ok(subscribed.has(`${H}/catalog/charts/all`));
  assert.equal(catalog.request('items', { kind: 'playlist', id: 'pl.9' }), true);
  assert.deepEqual(published.at(-1).payload, { action: 'catalog', what: 'items', kind: 'playlist', id: 'pl.9' });
  assert.ok(subscribed.has(`${H}/catalog/items/playlist/pl.9`));
  // The phone went away and came back: asked again at once.
  available(false, 'no_phone');
  catalog.statusChanged();
  available(true);
  catalog.statusChanged();
  assert.equal(catalog.request('charts', { genre: '21' }), true);
});

test('only the latest few long lists stay watched', () => {
  switchOn();
  available();
  for (let i = 0; i < 14; i++) catalog.watchList(catalog.itemsTopic('album', `x${i}`));
  assert.ok(!subscribed.has(`${H}/catalog/items/album/x0`));
  assert.ok(subscribed.has(`${H}/catalog/items/album/x13`));
});

test('charts: ranked playlists, albums, songs; the genre screen uses its genre', () => {
  switchOn();
  available();
  deliver(`${H}/catalog/genres`, genresPayload);
  catalog.watchList(catalog.chartsTopic('21'));
  deliver(`${H}/catalog/charts/21`, {
    v: 1, state: 'ready', at: 1, genre: { id: '21', name: 'Rock' },
    playlists: [playlist(1), playlist(2)], albums: [album(1)], songs: [track(1), track(2)],
  });
  const rock = { id: '21', name: 'Rock' };
  const p = catalog.blockView({ show: 'top-playlists' }, { screenGenre: rock });
  assert.deepEqual(p.cards.map(c => [c.kind, c.id, c.rankLabel, c.description]),
    [['playlist', 'pl.1', '#1', 'Apple Music Rock'], ['playlist', 'pl.2', '#2', 'Apple Music Rock']]);
  const a = catalog.blockView({ show: 'top-albums' }, { screenGenre: rock });
  assert.deepEqual(a.cards.map(c => [c.kind, c.id, c.rank, c.description]), [['album', '501', 0, 'Artist 1']]);
  const s = catalog.blockView({ show: 'top-songs', limit: 1 }, { screenGenre: rock });
  assert.equal(s.cards.length, 1);
  assert.equal(s.cards[0].track.item_path, 'helper:dev1/catalog:1001');
  assert.equal(s.cards[0].rankLabel, '1');
  // "which" names a genre anywhere; "genre" is that genre's playlists.
  const g = catalog.blockView({ show: 'genre', which: '21' });
  assert.equal(g.title, 'Rock Playlists');
  assert.equal(g.cards.length, 2);
  // Off the genre screen with no genre: the overall charts, not here yet.
  const overall = catalog.blockView({ show: 'top-playlists' });
  assert.deepEqual(overall.cards, []);
  assert.equal(overall.message, 'Loading…');
});

test('a list that failed says so; one that is loading keeps its old cards', () => {
  switchOn();
  available();
  catalog.watchList(catalog.chartsTopic('99'));
  deliver(`${H}/catalog/charts/99`, { v: 1, state: 'error', reason: 'offline', at: 1, playlists: [], albums: [], songs: [] });
  const v = catalog.blockView({ show: 'top-playlists', which: '99' });
  assert.equal(v.message, 'Apple Music can\'t be reached right now');
  assert.equal(v.loading, false);
  deliver(`${H}/catalog/charts/99`, { v: 1, state: 'loading', at: 1, playlists: [playlist(4)], albums: [], songs: [] });
  assert.equal(catalog.blockView({ show: 'top-playlists', which: '99' }).cards.length, 1);
});

test('the board shows a catalog playlist, picks its tracks, and Back leaves it', () => {
  switchOn();
  available();
  board.openCatalog('playlist', 'pl.7', 'Rock Hits');
  assert.deepEqual(published.at(-1).payload, { action: 'catalog', what: 'items', kind: 'playlist', id: 'pl.7' });
  // Not here yet: loading, empty slots.
  let v = board.view();
  assert.deepEqual(v.album, { loading: true });
  assert.ok(v.slots.every(s => !s.item));
  deliver(`${H}/catalog/items/playlist/pl.7`, {
    v: 1, state: 'ready', at: 1, kind: 'playlist', id: 'pl.7', name: 'Rock Hits', artist: null, cover: cover(7),
    songs: [1, 2, 3, 4, 5, 6, 7, 8].map(n => track(n)),
  });
  v = board.view();
  assert.equal(v.album, null);
  assert.equal(v.pages, 2);
  assert.deepEqual(v.slots.map(s => s.code), ['A1', 'A2', 'A3', 'B1', 'B2', 'B3']);
  assert.equal(v.slots[0].item.title, 'Song 1');
  assert.equal(v.filter.value, 'Rock Hits');
  // The keypad picks the track itself, exactly as jb-core expects a track.
  const picked = board.pick('A2');
  assert.equal(picked.item_path, 'helper:dev1/catalog:1002');
  assert.equal(picked.catalog, true);
  // No level above it on the board: Back goes to the screen it came from.
  assert.equal(board.upLevel(), false);
  assert.equal(board.showingCatalog(), true);
  assert.equal(catalog.catalogTitle('Rock Hits'), 'Apple Music · Rock Hits');
  assert.equal(catalog.catalogSubtitle('playlist', 'pl.7'), '8 songs');
  board.setSort('title');
  assert.equal(board.showingCatalog(), false);
});

test('the board says why a catalog list cannot load', () => {
  switchOn();
  available();
  board.openCatalog('album', '777', 'Some Album');
  deliver(`${H}/catalog/items/album/777`, { v: 1, state: 'error', reason: 'no_subscription', at: 1, songs: [] });
  assert.deepEqual(board.view().album, { failed: 'This phone has no Apple Music subscription' });
  available(false, 'no_phone');
  assert.deepEqual(board.view().album, { failed: 'Connect your phone' });
  board.setSort('title');
});

test('the Apple Music card takes two different genre covers', () => {
  switchOn();
  available();
  deliver(`${H}/catalog/genres`, genresPayload);
  const pick = (list, key) => list[key.endsWith('1') ? 0 : list.length - 1];
  const [a, b] = catalog.cardCovers(pick);
  assert.ok(a && b && a !== b);
});
