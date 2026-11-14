// The Apple Music block and its cards (docs/theme-format.md, "Blocks";
// docs/contracts/apple-music.md, section 3). The block shows the genres, or
// a chart's playlists, albums or songs, and fits its space: one card that
// opens the Apple Music screen (tile), a strip of cards (row) or a grid.
// The Apple Music screen and the genre screen are made of these blocks.
// The data and its states come from catalog.js.

import { bind, fromTemplate, slot } from './bind.js';
import { appleMusicOn, blockView, cardCovers, request, settingsOf, TICK } from './catalog.js';
import { makeStop } from './focus.js';
import { store } from './store.js';
import { steadyPick, toTrack } from './tracks.js';

// Each kind of card's markup (blocks/*.html; a theme can replace any).
const TEMPLATES = { genre: 'genre-tile', playlist: 'catalog-card', album: 'catalog-card', song: 'catalog-song' };
const CLASSES = { genre: 'jb-card-genre', playlist: 'jb-card-catalog', album: 'jb-card-catalog', song: 'jb-card-catalog-song' };

// One card, a knob stop: a genre opens the genre screen, a playlist or
// album opens its songs on the board, a song is picked like any other.
// `where` keeps its stop's name apart from the same card elsewhere.
export function catalogCard(item, ctx, where) {
  const card = document.createElement('div');
  card.className = `jb-card ${CLASSES[item.kind]}`;
  card.dataset.kind = item.kind;
  card.dataset.artScope = '';
  card.append(fromTemplate(ctx.blocks[TEMPLATES[item.kind]]));
  if (item.kind === 'song') {
    bind(card, { ...toTrack(item.track), rank: item.rank, rankLabel: item.rankLabel, explicit: !!item.track.explicit,
      playIcon: ctx.icons.play });
    makeStop(card, `${where}:song:${item.id}`, () => ctx.actions.pick(item.track));
  } else {
    bind(card, { ...item, openIcon: ctx.icons['card-open'] });
    makeStop(card, `${where}:${item.kind}:${item.id}`, item.kind === 'genre'
      ? () => ctx.actions.openGenre(item.genre)
      : () => ctx.actions.openCatalog(item.kind, item.id, item.label));
  }
  return card;
}

// The one card that opens the Apple Music screen (the Browse card, the
// block's tile shape): the Apple Music badge over two genre covers.
export function appleMusicLink(ctx) {
  const [image, image2] = cardCovers(steadyPick);
  return {
    label: 'Apple Music',
    description: 'Genres and charts',
    image,
    image2,
    badge: 'Apple Music',
    badgeIcon: ctx.icons['apple-music'],
    open: () => ctx.actions.openAppleMusic(),
  };
}

export function linkCard(item, ctx, key) {
  const card = document.createElement('div');
  card.className = 'jb-card jb-card-link jb-card-apple-music';
  card.dataset.artScope = '';
  card.append(fromTemplate(ctx.blocks['link-card-apple-music'] || ctx.blocks['link-card']));
  bind(card, { ...item, openIcon: ctx.icons['card-open'] });
  makeStop(card, key, item.open);
  return card;
}

// The genre the screen shows, on the genre screen (main.js sets it).
function screenGenre(ctx) {
  return ctx.screen === 'apple-music-genre' ? store.get('amGenre') || null : null;
}

// Whether the screen this block is on is the one showing (a block in the
// frame always is).
function onShow(ctx) {
  return !ctx.screen || store.get('screen') === ctx.screen;
}

// Settings: show (genres, top-playlists, top-albums, top-songs, genre),
// which (a genre id), limit, title; shape (tile, row, grid).
export function fillAppleMusic(el, node, ctx) {
  el.append(fromTemplate(ctx.blocks['apple-music']));
  const cards = slot(el, 'cards');
  const shapeOf = () => el.dataset.shape || node.shape || 'row';

  // Ask for what it shows each time its screen comes up (jb-source-helper
  // answers from its kept copy while that's fresh).
  const want = () => {
    if (!onShow(ctx) || !appleMusicOn()) return;
    const s = settingsOf(node, screenGenre(ctx));
    if (shapeOf() === 'tile' || s.show === 'genres') request('genres');
    else request('charts', { genre: s.genre });
  };

  let shown = null;
  const render = () => {
    const v = blockView(node, { shape: shapeOf(), screenGenre: screenGenre(ctx) });
    if (el.hidden !== !!v.hidden) el.hidden = !!v.hidden;
    if (v.hidden) {
      shown = null;
      return;
    }
    if (v.tile) {
      const item = appleMusicLink(ctx);
      const sig = `tile|${item.image}|${item.image2}`;
      if (sig === shown) return;
      shown = sig;
      bind(el, { title: '', message: '', detail: '', loading: false });
      el.toggleAttribute('data-empty', false);
      cards?.replaceChildren(linkCard(item, ctx, 'apple-music:tile'));
      return;
    }
    // Apple Music unavailable: the first block on the screen says why, the
    // rest stay out of the way (one "Connect your phone", not three).
    el.jbUnavailable = v.unavailable;
    const screen = el.closest('jb-screen > *') || el.parentElement;
    const first = !v.unavailable || !screen
      || [...screen.querySelectorAll('jb-apple-music')].find(b => !b.hidden && b.jbUnavailable) === el;
    const message = first ? v.message : '';
    const sig = JSON.stringify([shapeOf(), v.title, message, v.detail, v.cards.map(c => [c.kind, c.id, c.label, c.image, c.rank])]);
    if (sig === shown) return;
    shown = sig;
    // The reason stands alone, without the block's title over it.
    bind(el, { title: v.unavailable ? '' : v.title, message, detail: first ? v.detail : '', loading: v.loading && first });
    el.toggleAttribute('data-empty', !v.cards.length && !message);
    el.toggleAttribute('data-unavailable', !!v.unavailable);
    const where = `apple-music:${node.show || 'genres'}:${node.which || ''}`;
    cards?.replaceChildren(...v.cards.map(item => catalogCard(item, ctx, where)));
    fitRow();
  };

  // The row shape: one line of cards, as many as fit. The theme's CSS
  // decides how wide a card is (a grid that fills its columns, e.g.
  // repeat(auto-fill, minmax(19vh, 1fr))); the cards past the first line
  // are hidden, so they're no knob stops either. Measured only when the
  // cards or the space change, never on a knob step.
  function fitRow() {
    if (!cards) return;
    const row = shapeOf() === 'row';
    let across = Infinity;
    if (row && el.isConnected) {
      // "none": the theme lays them out some other way; it decides then.
      const cols = getComputedStyle(cards).gridTemplateColumns;
      if (cols && cols !== 'none') across = cols.split(' ').filter(Boolean).length;
    }
    [...cards.children].forEach((card, i) => {
      const hide = row && i >= across;
      if (card.hidden !== hide) card.hidden = hide;
    });
  }
  if (cards) new ResizeObserver(fitRow).observe(cards);

  // Its shape is measured once it's on the page (blocks.js, fitShape), and
  // again when its space changes.
  new MutationObserver(() => { render(); fitRow(); }).observe(el, { attributes: true, attributeFilter: ['data-shape'] });
  // Asked again when it could now be answered (switched on, a phone with
  // the app connected) or should be something else (another genre), never
  // on an answer arriving (that would ask on every answer).
  for (const key of ['helperSettings', 'catalogStatus', 'amGenre']) {
    store.watch(el, key, () => {
      render();
      want();
    });
  }
  store.watch(el, 'catalogGenres', render);
  store.watch(el, TICK, render);
  store.watch(el, 'screen', want);
}
