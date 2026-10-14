// Every block the engine knows. Each entry fills its element, or is null
// while the block isn't built yet (it then shows as a labeled outline).
// Keep the names in step with KNOWN_BLOCKS in services/jb_ui/themes.py.

import { bind, fromTemplate, slot } from './bind.js';
import { watchTopic } from './bus.js';
import { listData, listSongs, registerList } from './lists.js';
import { SORTS, turnPage, view } from './board.js';
import { makeStop } from './focus.js';
import { nowPlayingData } from './np.js';
import { fillCollection, fillSection, fillShortcut } from './sections.js';
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
  section: fillSection,
  collection: fillCollection,
  'page-info': fillPageInfo,
  pager: fillPager,
  header: fillHeader,
  connection: fillConnection,
  // Content the owner places with the page builder.
  heading: fillText('h2'),
  text: fillText('p'),
  image: fillImage,
  spacer: () => {},
  shortcut: fillShortcut,
};

// Blocks that fit themselves to the space they're given
// (docs/theme-format.md, "Shapes"): their shapes, narrowest first, each
// with the width it needs, as a share of the screen's height (so it holds
// at any resolution). The block carries the one that fits as data-shape;
// its "shape" setting can fix one instead.
export const SHAPES = {
  'now-playing': [['compact', 0], ['card', 0.42], ['banner', 0.95]],
  'up-next': [['compact', 0], ['strip', 0.6]],
  nav: [['compact', 0], ['bar', 0.6]],
  section: [['narrow', 0], ['medium', 0.7], ['wide', 1.15]],
};

const shapeWatch = new ResizeObserver(entries => {
  for (const { target, contentRect } of entries) {
    if (!target.isConnected) {
      shapeWatch.unobserve(target);
      continue;
    }
    const ratio = contentRect.width / window.innerHeight;
    let fits = target.jbShapes[0][0];
    for (const [name, from] of target.jbShapes) if (ratio >= from) fits = name;
    if (target.dataset.shape !== fits) target.dataset.shape = fits;
  }
});

export function fitShape(el, node, own) {
  const shapes = own?.length ? own : SHAPES[node.block];
  if (!shapes) return;
  if (shapes.some(([name]) => name === node.shape)) {
    el.dataset.shape = node.shape;
    return;
  }
  el.dataset.shape = shapes[0][0];
  el.jbShapes = shapes;
  shapeWatch.observe(el);
}

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
  img.src = assetUrl(node.src, ctx);
  img.alt = 'JUKE.BOX';
  img.onerror = text;
  el.append(img);
}

// A picture setting: a file in the theme ("assets/logo.png") or one the
// owner uploaded to the media library ("/media/photo.jpg").
function assetUrl(src, ctx) {
  return String(src).startsWith('/media/') ? String(src) : ctx.base + src;
}

// Heading and text blocks: the owner's words, always as plain text.
function fillText(tag) {
  return (el, node) => {
    const t = document.createElement(tag);
    t.className = `jb-${node.block}-text`;
    t.textContent = node.text ?? '';
    el.replaceChildren(t);
  };
}

function fillImage(el, node, ctx) {
  if (!node.src) {
    el.dataset.placeholder = 'image';
    return;
  }
  const img = document.createElement('img');
  img.src = assetUrl(node.src, ctx);
  img.alt = node.alt ?? '';
  el.replaceChildren(img);
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
  // Its header (data-stop="open") opens the Now Playing overlay.
  stopsFrom(el, 'now-playing', { open: () => ctx.actions.nav('now-playing') });
  // The card and banner shapes' background: the cover, blurred, or a
  // picture of the owner's. Set by hand, not data-src, so it never takes
  // over the cover's art colors.
  const backdrop = slot(el, 'backdrop');
  const background = node.background || 'cover';
  const picture = background === 'picture' && node.picture ? assetUrl(node.picture, ctx) : '';
  if (backdrop) backdrop.dataset.kind = background;
  const showBackdrop = url => {
    if (!backdrop || (backdrop.firstChild?.getAttribute('src') ?? '') === url) return;
    if (!url) return backdrop.replaceChildren();
    // The owner's picture may be a video: it plays, silent, on a loop.
    const video = /\.(mp4|m4v|mov|webm|mkv)$/i.test(url);
    const el = document.createElement(video ? 'video' : 'img');
    if (video) Object.assign(el, { autoplay: true, loop: true, muted: true, playsInline: true });
    else el.alt = '';
    el.src = url;
    backdrop.replaceChildren(el);
  };
  const render = () => {
    const d = nowPlayingData();
    el.dataset.state = d.state;
    el.style.setProperty('--jb-progress', d.progress.toFixed(4));
    bind(el, { ...d, headIcon: ctx.icons['now-playing'], tagline: node.tagline || '' });
    showBackdrop(background === 'cover' ? sized(d.cover, 200) || '' : picture);
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
  let lastTurn = 0;

  // A page turn swipes: the page slides out the way it's going and fades,
  // the new one slides in from the other side. Only transform and opacity
  // animate (cheap on the Pi). Themes can set --jb-page-ms (whole swipe)
  // and --jb-page-shift (how far, as a fraction of the board's width).
  const swipe = dir => {
    const style = getComputedStyle(el);
    const ms = parseFloat(style.getPropertyValue('--jb-page-ms')) || 360;
    const shift = (parseFloat(style.getPropertyValue('--jb-page-shift')) || 0.22) * el.clientWidth;
    el.getAnimations().forEach(a => a.cancel());
    const out = el.animate(
      [{ transform: 'translateX(0)', opacity: 1 }, { transform: `translateX(${-dir * shift}px)`, opacity: 0 }],
      { duration: ms * 0.42, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' });
    out.onfinish = () => {
      draw();
      out.cancel();
      el.animate(
        [{ transform: `translateX(${dir * shift}px)`, opacity: 0 }, { transform: 'translateX(0)', opacity: 1 }],
        { duration: ms * 0.58, easing: 'cubic-bezier(0.15, 0.85, 0.25, 1)' });
    };
  };

  const render = () => {
    const turn = view().turn;
    if (turn && turn.seq !== lastTurn) {
      lastTurn = turn.seq;
      swipe(turn.dir);
    } else if (!el.getAnimations().length) {
      draw();
    }
  };

  const draw = () => {
    const v = view();
    el.style.setProperty('--jb-digits', v.digits);
    el.toggleAttribute('data-category', v.isCategory);
    el.toggleAttribute('data-cards', !!v.cards);   // the album browser: cover cards
    el.toggleAttribute('data-loading', !!v.album?.loading);
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
      const item = slotInfo.item;
      s.append(fromTemplate(ctx.blocks[v.cards ? 'album-card' : 'song-card']));
      if (!item) {
        s.dataset.empty = '';
        bind(s, { code: slotInfo.code });
      } else if (v.cards) {
        bind(s, { code: slotInfo.code, title: item.category, cover: sized(item.cover, 400) });
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
  store.watch(el, 'playlists', render);
  store.watch(el, 'albums', render);
  store.watch(el, 'albumFetch', render);
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
  store.watch(el, 'playlists', render);
  store.watch(el, 'albums', render);
  store.watch(el, 'albumFetch', render);
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
      if (v.filter?.field === 'list') {
        // A module's playlist: its own name ("Rainy Day Records").
        const d = listData(v.filter.value);
        title = d?.hook || 'Mix';
        subtitle = [d?.subtitle, v.pages > 1 ? `Page ${v.page + 1} of ${v.pages}` : ''].filter(Boolean).join(' · ');
      }
      if (v.album?.loading) subtitle += ' · Loading album…';
      if (v.album?.failed) subtitle += ` · ${v.album.failed}`;
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
  store.watch(el, 'albums', render);
  store.watch(el, 'albumFetch', render);
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

// A block a module adds (docs/contracts/extensions.md, Blocks): its markup,
// filled from the latest message on its topic. Icon names in the message
// become the block's icons. Nothing until data arrives (data-empty).
export function fillModuleBlock(el, node, ctx, def) {
  el.append(fromTemplate(def.template));
  el.dataset.module = def.module;
  el.toggleAttribute('data-empty', true);
  watchTopic(def.topic);
  // A playlist block (lists.js): its template's data-stop="open" opens it
  // on the board, data-stop="play" plays it all; it gets count, cover1-4
  // (from its songs in the library), playIcon and openIcon too.
  const list = def.kind === 'playlist';
  if (list) {
    registerList(node.block, def);
    stopsFrom(el, `list:${node.block}`, { open: () => ctx.actions.openList(node.block), play: () => ctx.actions.playList(node.block) });
  }
  const render = () => {
    const data = store.get(`topic:${def.topic}`);
    let extra = {};
    if (list) {
      const songs = listSongs(node.block);
      const covers = [...new Set(songs.map(t => t.artwork_url).filter(Boolean))].slice(0, 4);
      extra = { count: songs.length, countLabel: `${songs.length} song${songs.length === 1 ? '' : 's'}`,
        playIcon: ctx.icons.play, openIcon: ctx.icons['card-open'] };
      covers.forEach((c, i) => { extra[`cover${i + 1}`] = sized(c, 200); });
      if (data && !songs.length) return el.toggleAttribute('data-empty', true);
    }
    el.toggleAttribute('data-empty', !data);
    if (!data || typeof data !== 'object') return;
    const icons = {};
    for (const [k, v] of Object.entries(data)) if (typeof v === 'string' && def.icons[v]) icons[k] = def.icons[v];
    bind(el, { ...data, ...icons, ...extra });
  };
  store.watch(el, `topic:${def.topic}`, render);
  if (list) store.watch(el, 'tracks', render);
}
