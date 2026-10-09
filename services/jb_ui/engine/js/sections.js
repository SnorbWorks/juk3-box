// Home sections (docs/theme-format.md, "Home sections"): rows of cards.
// Song cards pick their song; link cards open the board their way.

import { bind, fromTemplate, slot } from './bind.js';
import { SORTS } from './board.js';
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

// Each type: its heading, card kind, default card count, and its cards.
const SECTIONS = {
  new: {
    title: 'New',
    kind: 'song',
    limit: 4,
    // Newest to the jukebox first (jb-library's added_at); same-time
    // arrivals alphabetically.
    items: tracks => tracks.filter(t => t.added_at).sort((a, b) => b.added_at - a.added_at || byTitle(a, b)),
  },
  recents: {
    title: 'Recents',
    kind: 'song',
    limit: 6,
    items: tracks => tracks.filter(t => t.last_played_at).sort((a, b) => b.last_played_at - a.last_played_at),
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
        image: sized(f.artwork_url, 400),
        open: () => ctx.actions.openFilter('artist', f.artist),
      }];
    },
  },
  browse: {
    title: 'Browse',
    kind: 'link',
    limit: Infinity,
    items: (tracks, ctx) => ctx.settings.browse.map(sort => ({
      label: SORTS[sort]?.label || sort,
      description: BROWSE_DESCRIPTIONS[sort] || '',
      image: ctx.browseImages[sort] || '',
      open: () => ctx.actions.openSort(sort),
    })),
  },
};

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
  const template = def.kind === 'song'
    ? ctx.blocks[`song-tile-${node.type}`] || ctx.blocks['song-tile']
    : ctx.blocks[`link-card-${node.type}`] || ctx.blocks['link-card'];

  const render = () => {
    const items = def.items(store.get('tracks') || [], ctx).slice(0, limit);
    bind(el, { title: def.title, count: items.length });
    el.toggleAttribute('data-empty', !items.length);
    if (!cards) return;
    cards.replaceChildren(...items.map((item, i) => {
      const card = document.createElement('div');
      card.className = `jb-card jb-card-${def.kind}`;
      card.append(fromTemplate(template));
      if (def.kind === 'song') {
        const track = toTrack(item);
        bind(card, { ...track, cover: sized(track.cover, 400) });
        makeStop(card, `card:${node.type}:${item.title}`, () => ctx.actions.pick(item));
      } else {
        bind(card, item);
        makeStop(card, `card:${node.type}:${item.label}`, item.open);
      }
      return card;
    }));
  };
  store.watch(el, 'tracks', render);
  store.watch(el, 'featured', render);
}
