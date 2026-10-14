// Home sections (docs/theme-format.md, "Home sections"): rows of cards,
// each a module placed in a layout with its own settings (like a page
// builder's modules). Song cards pick their song; link cards open the board
// their way. "See All" opens the section's full card view (the
// collection screen).

import { bind, fromTemplate, slot } from './bind.js';
import { SORTS, coversFor, playlistSongs } from './board.js';
import { featuredFor, headingOf, KINDS } from './featured.js';
import { makeStop } from './focus.js';
import { store } from './store.js';
import { sized, toTrack } from './tracks.js';

const byTitle = (a, b) => (a.title || '').localeCompare(b.title || '');

// The Browse module's categories unless its "sorts" setting says otherwise.
const DEFAULT_SORTS = ['artist', 'album', 'top-100', 'most-played'];

const BROWSE_DESCRIPTIONS = {
  title: 'Every song',
  artist: 'Every artist',
  album: 'Every album',
  genre: 'Every genre',
  playlist: 'Your playlists',
  'top-100': "The world's favorites",
  'most-played': 'Played most here',
};

// A Browse card with no picture from the theme shows a random cover from
// its own songs. Kept for half an hour (the "featured" tick), so cards
// don't change on every re-render.
const browseCovers = new Map();
let coversFeatured;

function randomCover(sort) {
  const featured = store.get('featured');
  if (featured !== coversFeatured) {
    browseCovers.clear();
    coversFeatured = featured;
  }
  const pool = coversFor(sort);
  if (!browseCovers.has(sort) || !pool.includes(browseCovers.get(sort))) {
    browseCovers.set(sort, pool[Math.floor(Math.random() * pool.length)] || '');
  }
  return browseCovers.get(sort);
}

function browseCard(sort, ctx) {
  return {
    label: SORTS[sort]?.label || sort,
    description: BROWSE_DESCRIPTIONS[sort] || '',
    image: ctx.browseImages[sort] || sized(randomCover(sort), 600),
    open: () => ctx.actions.openSort(sort),
  };
}

// Each type: its heading, card kind, default card count, its cards, and
// its See All view (how many, and how it's titled).
const SECTIONS = {
  new: {
    title: 'New',
    kind: 'song',
    limit: 2,
    // Newest to the jukebox first (jb-library's added_at); same-time
    // arrivals alphabetically.
    items: tracks => tracks.filter(t => t.added_at).sort((a, b) => b.added_at - a.added_at || byTitle(a, b)),
    all: { limit: 100, subtitle: n => `${n} new song${n === 1 ? '' : 's'}` },
  },
  recents: {
    title: 'Recents',
    kind: 'song',
    limit: 18,
    items: tracks => tracks.filter(t => t.last_played_at).sort((a, b) => b.last_played_at - a.last_played_at),
    all: { limit: 100, subtitle: n => `The last ${n} played` },
  },
  // "Featured" in the admin: one artist, album, genre or playlist, picked
  // by the block's settings (featured.js). The type keeps its first name
  // so layouts and themes made for it still work.
  'featured-artist': {
    title: 'Featured Artist',
    heading: node => headingOf(node),
    kind: 'link',
    limit: 1,
    items: (tracks, ctx, node, el) => {
      const f = el && featuredFor(el, node, tracks);
      if (!f) return [];
      const field = KINDS[node.feature] ? KINDS[node.feature].field : 'artist';
      const n = f.songs.length;
      const songs = `${n} song${n === 1 ? '' : 's'}`;
      const artists = [...new Set(f.songs.map(t => t.artist).filter(Boolean))];
      return [{
        label: f.value,
        kicker: headingOf(node),
        description: field === 'album' && artists.length === 1 ? `${artists[0]} · ${songs}` : songs,
        image: sized(f.cover, 600),
        backdrop: sized(f.cover, 24),
        open: () => ctx.actions.openFilter(field, f.value),
      }];
    },
  },
  // The phone's own playlists, one card each; a card opens the board on
  // that playlist's songs, in the playlist's order.
  playlists: {
    title: 'Playlists',
    kind: 'link',
    limit: 12,
    items: (tracks, ctx) => (store.get('playlists') || []).map(p => {
      const songs = playlistSongs(tracks, p.name);
      const cover = songs.find(t => t.artwork_url)?.artwork_url;
      return {
        label: p.name,
        description: `${songs.length} song${songs.length === 1 ? '' : 's'}`,
        image: sized(cover, 600),
        backdrop: sized(cover, 24),
        open: () => ctx.actions.openFilter('playlist', p.name),
      };
    }).filter(item => item.description !== '0 songs'),
    all: { limit: Infinity, subtitle: n => `${n} playlist${n === 1 ? '' : 's'}` },
  },
  browse: {
    title: 'Browse',
    kind: 'link',
    limit: Infinity,
    items: (tracks, ctx, node) => sortsSetting(node).map(sort => browseCard(sort, ctx)),
    // See All: every way to browse, not just the owner's chosen four.
    all: {
      limit: Infinity,
      items: (tracks, ctx) => Object.keys(SORTS).map(sort => browseCard(sort, ctx)),
      subtitle: () => 'Every way to browse',
    },
  },
};

// The "sorts" setting: a comma-separated list, e.g. "artist,genre,top-100".
function sortsSetting(node) {
  const list = String(node?.sorts || '').split(',').map(s => s.trim()).filter(s => SORTS[s]);
  return list.length ? list : DEFAULT_SORTS;
}

function off(value) {
  return value === false || value === 'false';
}

function cardTemplate(def, type, ctx) {
  return def.kind === 'song'
    ? ctx.blocks[`song-tile-${type}`] || ctx.blocks['song-tile']
    : ctx.blocks[`link-card-${type}`] || ctx.blocks['link-card'];
}

// One card: a stop, with its colors sampled from its picture.
function makeCard(item, type, def, template, ctx) {
  const card = document.createElement('div');
  card.className = `jb-card jb-card-${def.kind}`;
  card.dataset.artScope = '';
  card.append(fromTemplate(template));
  if (def.kind === 'song') {
    const track = toTrack(item);
    // backdrop: a tiny copy that looks blurred when stretched, so themes
    // needn't use a filter (a blur filter on a moving card stalls the Pi).
    bind(card, { ...track, cover: sized(track.cover, 600), backdrop: sized(track.cover, 24), playIcon: ctx.icons.play });
    makeStop(card, `card:${type}:${item.title}`, () => ctx.actions.pick(item));
  } else {
    bind(card, { ...item, openIcon: ctx.icons['card-open'] });
    makeStop(card, `card:${type}:${item.label}`, item.open);
  }
  return card;
}

// One section module. Settings: type, limit, title, sorts (Browse),
// see-all (true unless false), show (false hides it).
export function fillSection(el, node, ctx) {
  const def = SECTIONS[node.type];
  if (!def || off(node.show)) {
    el.hidden = true;
    return;
  }
  el.dataset.type = node.type;
  el.append(fromTemplate(ctx.blocks.section));
  const cards = slot(el, 'cards');
  const limit = Number(node.limit) || def.limit;
  const template = cardTemplate(def, node.type, ctx);
  const seeAll = el.querySelector('[data-stop="see-all"]');
  const hasSeeAll = !!def.all && !off(node['see-all']);
  if (seeAll && hasSeeAll) makeStop(seeAll, `see-all:${node.type}`, () => ctx.actions.openCollection(node.type));

  let shown = null;
  const render = () => {
    // A Featured block picks once it's on the page (its place there tells
    // it apart from other Featured blocks).
    if (def.heading && !el.isConnected) return requestAnimationFrame(() => el.isConnected && render());
    const tracks = store.get('tracks') || [];
    const items = def.items(tracks, ctx, node, el).slice(0, limit);
    // Unchanged: leave the cards be (a focused card keeps its focus).
    const sig = JSON.stringify(items.map(i => [i.title, i.artist, i.artwork_url, i.label, i.image, i.description]));
    if (sig === shown) return;
    shown = sig;
    bind(el, {
      title: node.title || (def.heading ? def.heading(node) : def.title),
      count: items.length,
      more: hasSeeAll && items.length > 0,
      seeAll: 'See All',
      seeAllIcon: ctx.icons['see-all'],
    });
    el.toggleAttribute('data-empty', !items.length);
    cards?.replaceChildren(...items.map(item => makeCard(item, node.type, def, template, ctx)));
  };
  store.watch(el, 'tracks', render);
  store.watch(el, 'featured', render);
  store.watch(el, 'playlists', render);
}

// A section's See All view: all its cards, in a grid the knob scrolls.
export function sectionView(type, ctx) {
  const def = SECTIONS[type];
  if (!def?.all) return null;
  const tracks = store.get('tracks') || [];
  const items = (def.all.items || def.items)(tracks, ctx).slice(0, def.all.limit);
  return { type, title: def.title, subtitle: def.all.subtitle(items.length) };
}

export function fillCollection(el, node, ctx) {
  const render = () => {
    const c = store.get('collection');
    const def = SECTIONS[c?.type];
    el.dataset.type = c?.type || '';
    if (!def?.all) return el.replaceChildren();
    const tracks = store.get('tracks') || [];
    const items = (def.all.items || def.items)(tracks, ctx).slice(0, def.all.limit);
    const template = cardTemplate(def, c.type, ctx);
    el.replaceChildren(...items.map(item => makeCard(item, c.type, def, template, ctx)));
  };
  store.watch(el, 'collection', render);
  store.watch(el, 'tracks', render);
  store.watch(el, 'playlists', render);
}
