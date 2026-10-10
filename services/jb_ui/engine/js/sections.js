// Home sections (docs/theme-format.md, "Home sections"): rows of cards.
// Song cards pick their song; link cards open the board their way. "See
// All" opens a section's full card view (the collection screen).

import { bind, fromTemplate, slot } from './bind.js';
import { SORTS, coversFor } from './board.js';
import { makeStop } from './focus.js';
import { store } from './store.js';
import { sized, toTrack } from './tracks.js';

const byTitle = (a, b) => (a.title || '').localeCompare(b.title || '');

const BROWSE_DESCRIPTIONS = {
  title: 'Every song',
  artist: 'Every artist',
  album: 'Every album',
  genre: 'Every genre',
  'top-100': "The world's favorites",
  'most-played': 'Played most here',
};

// A Browse card with no picture from the theme shows a random cover from
// its own songs. Kept until the featured artist changes (every 30 minutes),
// so cards don't change on every re-render.
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
  'featured-artist': {
    title: 'Featured Artist',
    kind: 'link',
    limit: 1,
    items: (tracks, ctx) => {
      const f = store.get('featured');
      if (!f) return [];
      const count = tracks.filter(t => t.artist === f.artist).length;
      return [{
        label: f.artist,
        description: `${count} song${count === 1 ? '' : 's'}`,
        image: sized(f.artwork_url, 600),
        open: () => ctx.actions.openFilter('artist', f.artist),
      }];
    },
  },
  browse: {
    title: 'Browse',
    kind: 'link',
    limit: Infinity,
    items: (tracks, ctx) => ctx.settings.browse.map(sort => browseCard(sort, ctx)),
    // See All: every way to browse, not just the owner's chosen four.
    all: {
      limit: Infinity,
      items: (tracks, ctx) => Object.keys(SORTS).map(sort => browseCard(sort, ctx)),
      subtitle: () => 'Every way to browse',
    },
  },
};

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
    bind(card, { ...track, cover: sized(track.cover, 600), playIcon: ctx.icons.play });
    makeStop(card, `card:${type}:${item.title}`, () => ctx.actions.pick(item));
  } else {
    bind(card, { ...item, openIcon: ctx.icons['card-open'] });
    makeStop(card, `card:${type}:${item.label}`, item.open);
  }
  return card;
}

// The owner's chosen sections, in their order.
export function fillSections(el, node, ctx) {
  el.replaceChildren(...ctx.settings.home_sections.map(type => {
    const section = document.createElement('jb-section');
    section.setAttribute('type', type);
    fillSection(section, { type }, ctx);
    return section;
  }));
}

// One section, placed by the owner's list or pinned by a layout.
export function fillSection(el, node, ctx) {
  const def = SECTIONS[node.type];
  if (!def) return;
  el.dataset.type = node.type;
  el.append(fromTemplate(ctx.blocks.section));
  const cards = slot(el, 'cards');
  const limit = Number(node.limit) || def.limit;
  const template = cardTemplate(def, node.type, ctx);
  const seeAll = el.querySelector('[data-stop="see-all"]');
  if (seeAll && def.all) makeStop(seeAll, `see-all:${node.type}`, () => ctx.actions.openCollection(node.type));

  const render = () => {
    const tracks = store.get('tracks') || [];
    const all = def.items(tracks, ctx);
    const items = all.slice(0, limit);
    const total = def.all?.items ? def.all.items(tracks, ctx).length : all.length;
    bind(el, {
      title: def.title,
      count: items.length,
      more: !!def.all && total > items.length,
      seeAll: 'See All',
      seeAllIcon: ctx.icons['see-all'],
    });
    el.toggleAttribute('data-empty', !items.length);
    cards?.replaceChildren(...items.map(item => makeCard(item, node.type, def, template, ctx)));
  };
  store.watch(el, 'tracks', render);
  store.watch(el, 'featured', render);
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
}
