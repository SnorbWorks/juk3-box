// Every block the engine knows. Each entry fills its element, or is null
// while the block isn't built yet (it then shows as a labeled outline).
// Keep the names in step with KNOWN_BLOCKS in services/jb_ui/themes.py.

import { bind, fromTemplate, slot } from './bind.js';
import { turnPage, upLevel, view } from './board.js';
import { makeStop } from './focus.js';
import { fillSection, fillSections } from './sections.js';
import { store } from './store.js';
import { formatMs, sized, toTrack } from './tracks.js';

export const BLOCKS = {
  // The engine puts the current screen into this element itself.
  screen: () => {},
  logo: fillLogo,
  nav: fillNav,
  'now-playing': fillNowPlaying,
  'up-next': fillUpNext,
  board: fillBoard,
  sections: fillSections,
  section: fillSection,
  'page-info': fillPageInfo,
  pager: fillPager,
};

function fillLogo(el, node, ctx) {
  const text = () => {
    el.replaceChildren();
    const span = document.createElement('span');
    span.className = 'jb-logo-text';
    span.textContent = 'JUKE.BOX';
    el.append(span);
  };
  if (!node.src) return text();
  const img = document.createElement('img');
  img.src = ctx.base + node.src;
  img.alt = 'JUKE.BOX';
  img.onerror = text;
  el.append(img);
}

// The phone reports its position every few seconds; between reports the
// elapsed time and progress run on locally so the bar moves smoothly.
const TICK_MS = 500;

function fillNowPlaying(el, node, ctx) {
  el.append(fromTemplate(ctx.blocks['now-playing']));
  let np = null;
  let receivedAt = 0;

  const render = () => {
    const state = np?.status === 'playing' ? 'playing' : np?.status === 'paused' ? 'paused' : 'stopped';
    const track = np?.title ? toTrack(np, store.get('library')) : {};
    let position = np?.position_ms ?? 0;
    if (state === 'playing') position += Date.now() - receivedAt;
    const duration = np?.duration_ms;
    if (duration) position = Math.min(position, duration);

    el.dataset.state = state;
    el.style.setProperty('--jb-progress', duration ? (position / duration).toFixed(4) : '0');
    bind(el, { ...track, state, elapsed: np?.title ? formatMs(position) : '' });
  };

  store.watch(el, 'nowPlaying', value => {
    np = value || null;
    receivedAt = Date.now();
    render();
  });
  // Covers arrive with the library, possibly after the song started.
  store.watch(el, 'library', render);

  const timer = setInterval(() => {
    if (!el.isConnected) return clearInterval(timer);
    if (np?.status === 'playing') render();
  }, TICK_MS);
}

function fillUpNext(el, node, ctx) {
  el.append(fromTemplate(ctx.blocks['up-next']));
  const cards = slot(el, 'cards');
  const limit = Number(node.limit) || Infinity;

  const render = () => {
    const upcoming = store.get('queue')?.upcoming || [];
    const library = store.get('library');
    bind(el, { count: upcoming.length });
    if (!cards) return;
    cards.replaceChildren(...upcoming.slice(0, limit).map((raw, i) => {
      const card = fromTemplate(ctx.blocks['queue-card']);
      const track = toTrack(raw, library);
      bind(card, { ...track, cover: sized(track.cover, 200), position: i + 1 });
      return card;
    }));
  };

  store.watch(el, 'queue', render);
  store.watch(el, 'library', render);
}

const NAV_LABELS = {
  home: 'Home',
  volume: 'Volume',
  shuffle: 'Shuffle',
  'all-songs': 'All Songs',
  settings: 'Settings',
};

// The owner picks which buttons show, and their order (Settings). Each
// button sits in a .jb-nav-item, which carries its states.
function fillNav(el, node, ctx) {
  const items = ctx.settings.nav.map(name => {
    const item = document.createElement('div');
    item.className = 'jb-nav-item';
    item.dataset.button = name;
    item.append(fromTemplate(ctx.blocks['nav-button']));
    makeStop(item, `nav:${name}`, () => ctx.actions.nav(name));
    return item;
  });
  el.replaceChildren(...items);

  const render = () => {
    const shuffle = !!store.get('queue')?.shuffle_active;
    const screen = store.get('screen');
    for (const item of items) {
      const name = item.dataset.button;
      const on = name === 'shuffle' && shuffle;
      // Shuffle on reads "Singles": the label says what a click does.
      bind(item, {
        label: on ? 'Singles' : NAV_LABELS[name],
        icon: (on && ctx.icons['shuffle-on']) || ctx.icons[name] || '',
      });
      item.toggleAttribute('data-active', name === screen);
      if (name === 'shuffle') item.toggleAttribute('data-on', on);
    }
  };
  store.watch(el, 'queue', render);
  store.watch(el, 'screen', render);
}

// The song board: one page of slots, grouped by letter. Slot codes match
// the keypad; how they're arranged is the theme's CSS.
function fillBoard(el, node, ctx) {
  const groups = new Map();

  const render = () => {
    const v = view();
    el.style.setProperty('--jb-digits', v.digits);
    el.toggleAttribute('data-category', v.isCategory);
    groups.clear();
    for (const slotInfo of v.slots) {
      let group = groups.get(slotInfo.letter);
      if (!group) {
        group = document.createElement('div');
        group.className = 'jb-letter';
        group.dataset.letter = slotInfo.letter;
        groups.set(slotInfo.letter, group);
      }
      const s = document.createElement('div');
      s.className = 'jb-slot';
      s.dataset.code = slotInfo.code;
      s.append(fromTemplate(ctx.blocks['song-card']));
      const item = slotInfo.item;
      if (!item) {
        s.dataset.empty = '';
        bind(s, { code: slotInfo.code });
      } else if (item.category !== undefined) {
        // An artist, album or genre: picking it shows its songs.
        bind(s, { code: slotInfo.code, title: item.category });
      } else {
        const track = toTrack(item);
        bind(s, { ...track, cover: sized(track.cover, 200), code: slotInfo.code });
      }
      group.append(s);
    }
    el.replaceChildren(...groups.values());
    showEntry();
  };

  // A letter pressed on the keypad, waiting for its digit.
  const showEntry = () => {
    const e = store.get('entry');
    const waiting = e?.state === 'partial' ? e.letter : null;
    for (const [letter, group] of groups) {
      const on = letter === waiting;
      group.toggleAttribute('data-entry', on);
      for (const s of group.children) s.toggleAttribute('data-entry', on);
    }
  };

  store.watch(el, 'tracks', render);
  store.watch(el, 'board', render);
  store.watch(el, 'entry', showEntry);
}

function fillPageInfo(el, node, ctx) {
  el.append(fromTemplate(ctx.blocks['page-info']));
  const render = () => {
    const v = view();
    bind(el, { sort: v.sortLabel, filter: v.filter?.value || '', page: v.page + 1, pages: v.pages });
  };
  store.watch(el, 'tracks', render);
  store.watch(el, 'board', render);
}

// Knob stops for paging the board, and for backing out of an artist's
// (album's, genre's) songs to the list of them.
const PAGER = {
  prev: { label: () => 'Previous Page', click: () => turnPage(-1) },
  up: { label: v => `All ${v.sortLabel}`, click: upLevel },
  next: { label: () => 'Next Page', click: () => turnPage(1) },
};

function fillPager(el, node, ctx) {
  const items = Object.entries(PAGER).map(([name, b]) => {
    const item = document.createElement('div');
    item.className = 'jb-pager-item';
    item.dataset.button = name;
    item.append(fromTemplate(ctx.blocks['pager-button']));
    makeStop(item, `pager:${name}`, b.click);
    return item;
  });
  el.replaceChildren(...items);
  const render = () => {
    const v = view();
    for (const item of items) {
      const name = item.dataset.button;
      bind(item, { label: PAGER[name].label(v), icon: ctx.icons[`page-${name}`] || '' });
      // Only there while inside an artist (album, genre).
      if (name === 'up') item.hidden = !v.filter;
    }
  };
  store.watch(el, 'tracks', render);
  store.watch(el, 'board', render);
}
