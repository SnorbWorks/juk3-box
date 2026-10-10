// Every block the engine knows. Each entry fills its element, or is null
// while the block isn't built yet (it then shows as a labeled outline).
// Keep the names in step with KNOWN_BLOCKS in services/jb_ui/themes.py.

import { bind, fromTemplate, slot } from './bind.js';
import { SORTS, turnPage, view } from './board.js';
import { makeStop } from './focus.js';
import { nowPlayingData } from './np.js';
import { fillCollection, fillSection, fillSections } from './sections.js';
import { store } from './store.js';
import { sized, toTrack } from './tracks.js';

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
  collection: fillCollection,
  'page-info': fillPageInfo,
  pager: fillPager,
  header: fillHeader,
  connection: fillConnection,
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

// Elements in a template marked data-stop="name" become knob stops.
export function stopsFrom(root, keyPrefix, handlers) {
  for (const el of root.querySelectorAll('[data-stop]')) {
    const handler = handlers[el.dataset.stop];
    if (handler) makeStop(el, `${keyPrefix}:${el.dataset.stop}`, handler);
  }
}

const TICK_MS = 500;

function fillNowPlaying(el, node, ctx) {
  el.append(fromTemplate(ctx.blocks['now-playing']));
  el.dataset.artScope = '';
  const render = () => {
    const d = nowPlayingData();
    el.dataset.state = d.state;
    el.style.setProperty('--jb-progress', d.progress.toFixed(4));
    bind(el, d);
  };
  store.watch(el, 'nowPlaying', render);
  // Covers arrive with the library, possibly after the song started.
  store.watch(el, 'library', render);
  const timer = setInterval(() => {
    if (!el.isConnected) return clearInterval(timer);
    if (store.get('nowPlaying')?.status === 'playing') render();
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
      const card = document.createElement('div');
      card.className = 'jb-queue-item';
      card.dataset.artScope = '';
      card.append(fromTemplate(ctx.blocks['queue-card']));
      const track = toTrack(raw, library);
      bind(card, { ...track, cover: sized(track.cover, 200), position: i + 1 });
      return card;
    }));
  };

  store.watch(el, 'queue', render);
  store.watch(el, 'library', render);
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
      s.dataset.artScope = '';
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

// Knob stops for paging the board.
const PAGER = {
  prev: { label: 'Previous Page', icon: 'page-prev', click: () => turnPage(-1) },
  next: { label: 'Next Page', icon: 'page-next', click: () => turnPage(1) },
};

function fillPager(el, node, ctx) {
  el.replaceChildren(...Object.entries(PAGER).map(([name, b]) => {
    const item = document.createElement('div');
    item.className = 'jb-pager-item';
    item.dataset.button = name;
    item.append(fromTemplate(ctx.blocks['pager-button']));
    bind(item, { label: b.label, icon: ctx.icons[b.icon] });
    makeStop(item, `pager:${name}`, b.click);
    return item;
  }));
}

const NAV_LABELS = {
  home: 'Home',
  volume: 'Volume',
  'all-songs': 'All Songs',
  settings: 'Settings',
  'now-playing': 'Now Playing',
  shuffle: 'Shuffle',
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
      // Shuffle on reads "Single Play": the label says what a click does.
      bind(item, {
        label: on ? 'Single Play' : NAV_LABELS[name],
        icon: (on && ctx.icons['shuffle-on']) || ctx.icons[name],
      });
      item.toggleAttribute('data-active', name === screen);
      if (name === 'shuffle') item.toggleAttribute('data-on', on);
    }
  };
  store.watch(el, 'queue', render);
  store.watch(el, 'screen', render);
}

// The screen's title, subtitle and a Back button. On the board: the sort
// ("Browse by Genre") and the artist/album/genre picked ("Classic Rock"),
// else the page; on a See All view, its title and count.
function fillHeader(el, node, ctx) {
  el.append(fromTemplate(ctx.blocks.header));
  stopsFrom(el, 'header', { back: () => ctx.actions.back() });
  const render = () => {
    const screen = store.get('screen');
    let title = '', subtitle = '';
    if (screen === 'all-songs') {
      const v = view();
      const sort = SORTS[v.sort];
      title = sort?.category ? `Browse by ${sort.singular}` : v.sort === 'title' ? 'All Songs' : v.sortLabel;
      subtitle = v.filter ? v.filter.value : v.pages > 1 ? `Page ${v.page + 1} of ${v.pages}` : '';
    } else if (screen === 'collection') {
      const c = store.get('collection');
      title = c?.title || '';
      subtitle = c?.subtitle || '';
    }
    bind(el, { title, subtitle, canGoBack: screen !== 'home', backIcon: ctx.icons.back });
  };
  store.watch(el, 'screen', render);
  store.watch(el, 'board', render);
  store.watch(el, 'tracks', render);
  store.watch(el, 'collection', render);
}

// The phone connection badge. Clicking it will manage phones (connect,
// switch, forget) once the phone connection manager is built; for now it
// shows the connected phone.
function fillConnection(el, node, ctx) {
  el.append(fromTemplate(ctx.blocks.connection));
  makeStop(el, 'connection', () => ctx.actions.phones());
  store.watch(el, 'phone', p => {
    el.toggleAttribute('data-connected', !!p?.connected);
    bind(el, {
      status: p?.connected ? 'Connected' : 'Not connected',
      name: p?.connected ? p.name : 'Connect a phone',
      icon: ctx.icons.connection,
      moreIcon: ctx.icons.dropdown,
    });
  });
}
