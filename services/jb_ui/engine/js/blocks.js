// Every block the engine knows. Each entry fills its element, or is null
// while the block isn't built yet (it then shows as a labeled outline).
// Keep the names in step with KNOWN_BLOCKS in services/jb_ui/themes.py.

import { bind, fromTemplate, slot } from './bind.js';
import { store } from './store.js';
import { formatMs, toTrack } from './tracks.js';

export const BLOCKS = {
  // The engine puts the current screen into this element itself.
  screen: () => {},
  logo: fillLogo,
  nav: null,
  'now-playing': fillNowPlaying,
  'up-next': fillUpNext,
  board: null,
  sections: null,
  section: null,
  'page-info': null,
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
      bind(card, { ...toTrack(raw, library), position: i + 1 });
      return card;
    }));
  };

  store.watch(el, 'queue', render);
  store.watch(el, 'library', render);
}
