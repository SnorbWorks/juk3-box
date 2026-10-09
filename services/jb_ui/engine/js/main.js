// JUKE.BOX UI engine: loads the active theme and builds the screen from
// its layouts. Behavior lives here; the look lives in the theme.

import { openFilter, pick, setSort } from './board.js';
import { connectBus, publish } from './bus.js';
import { startFeatured } from './featured.js';
import { click, currentMode, enterMode, goToRest, refocus, rotate } from './focus.js';
import { renderLayout } from './layout.js';
import { closePopup, initPopups, openPopup, popupOpen, updatePopup } from './popup.js';
import { store } from './store.js';
import { fetchTheme, applyTheme } from './theme.js';

const VOLUME_STEP = 0.05;   // per knob detent
const NOTICE_MS = 4000;
const IDLE_CHECK_MS = 10000;

let theme = null;
const startedAt = Date.now();
const html = document.documentElement;

const actions = {
  nav(name) {
    if (name === 'volume') volumeMode();
    else if (name === 'shuffle') toggleShuffle();
    else {
      // All Songs means all of them: a plain title list, from page 1.
      if (name === 'all-songs') setSort('title');
      showScreen(name);
    }
  },
  // A song card or a keypad pick: jb-core plays it now if nothing is
  // playing, otherwise queues it.
  pick(track) {
    publish('jukebox/jb-core/cmd', { action: 'select', track });
  },
  openSort(sort) {
    setSort(sort);
    showScreen('all-songs');
  },
  openFilter(field, value) {
    openFilter(field, value);
    showScreen('all-songs');
  },
};

function context() {
  return {
    base: theme.base,
    blocks: theme.blocks,
    icons: theme.icons,
    browseImages: theme.browse_images,
    settings: theme.settings,
    actions,
  };
}

function showScreen(name) {
  const holder = document.querySelector('jb-screen');
  if (!holder) return;
  const layout = theme.layouts[name];
  if (layout) {
    holder.replaceChildren(renderLayout(layout, context()));
  } else {
    // The theme has no layout for this screen (only home and all-songs
    // are required). The engine's own version comes later; for now, say so.
    const missing = document.createElement('div');
    missing.dataset.placeholder = name;
    holder.replaceChildren(missing);
  }
  html.dataset.screen = name;
  store.set('screen', name);
  refocus();
}

function setVolumeVar(level) {
  html.style.setProperty('--jb-volume', level.toFixed(3));
}

// Turning the knob changes the volume until a click or a few idle seconds.
function volumeMode() {
  let level = store.get('volume')?.volume ?? 0;
  const show = () => {
    setVolumeVar(level);
    updatePopup('volume', { title: 'Volume', percent: Math.round(level * 100) });
  };
  openPopup('volume', {});
  show();
  enterMode('volume', {
    rotate(steps) {
      const delta = steps * VOLUME_STEP;
      level = Math.min(1, Math.max(0, level + delta));
      publish('jukebox/jb-audio/cmd', { action: 'volume_delta', value: delta });
      show();
    },
    exit() {
      closePopup('volume');
    },
  });
}

function toggleShuffle() {
  const on = !!store.get('queue')?.shuffle_active;
  // jb-core answers with a new queue state, which updates the button.
  publish('jukebox/jb-core/cmd', { action: on ? 'shuffle_off' : 'shuffle_on' });
}

function watchSystem() {
  store.watch(html, 'phone', p => { html.dataset.phone = p?.connected ? 'connected' : 'disconnected'; });

  // While the knob is setting the volume, its own running total is what's
  // shown: a confirmation for an older step can arrive after newer steps
  // and would snap the display backwards.
  store.watch(html, 'volume', v => {
    if (typeof v?.volume === 'number' && currentMode() !== 'volume') setVolumeVar(v.volume);
  });

  // A notice from jb-core shows briefly, unless the knob is busy.
  store.watch(html, 'notice', n => {
    if (n?.type !== 'connect_device' || currentMode() !== 'select' || popupOpen()) return;
    openPopup('notice', { title: 'Connect your phone', message: 'Connect your phone over Bluetooth to play music.' });
    setTimeout(() => closePopup('notice'), NOTICE_MS);
  });

  // The keypad: a letter brings up the board (so you can see what you're
  // picking), and letter + digit picks that slot. Picking an artist
  // (album, genre) opens its songs; picking a song sends it to jb-core,
  // which plays it now or queues it.
  store.watch(html, 'entry', e => {
    if (e?.state === 'partial' && store.get('screen') !== 'all-songs') showScreen('all-songs');
  });
  store.watch(html, 'selection', s => {
    if (!Number.isInteger(s?.index)) return;
    const track = pick(s.index);
    if (track) actions.pick(track);
  });
}

// Idle with nothing playing for the owner's set time: back to Home. The
// highlight returns to rest much sooner (focus.js); the screen only moves
// once the jukebox is truly unattended.
function watchIdle() {
  setInterval(() => {
    const minutes = theme.settings.idle_home_minutes;
    if (!minutes || store.get('screen') === 'home') return;
    if (store.get('nowPlaying')?.status === 'playing') return;
    const last = Math.max(store.get('activity') || 0, startedAt);
    if (Date.now() - last >= minutes * 60000) showScreen('home');
  }, IDLE_CHECK_MS);
}

// A real keyboard works too, for development: arrows turn, Enter clicks.
function watchKeyboard() {
  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowRight') rotate(1);
    else if (e.key === 'ArrowLeft') rotate(-1);
    else if (e.key === 'Enter') click();
  });
}

async function start() {
  theme = await fetchTheme();
  await applyTheme(theme);
  store.set('settings', theme.settings);
  initPopups(theme.blocks);
  watchSystem();
  watchIdle();
  startFeatured();
  document.getElementById('jb-root').replaceChildren(renderLayout(theme.layouts.frame, context()));
  showScreen('home');
  goToRest();
  watchKeyboard();
  connectBus();
}

start().catch(e => {
  console.error(e);
  const box = document.createElement('div');
  box.id = 'jb-error';
  box.textContent = `JUKE.BOX couldn't start its screen.\n\n${e.message}`;
  document.getElementById('jb-root').replaceChildren(box);
  // Keep trying: jb-ui may simply still be starting.
  setTimeout(() => location.reload(), 5000);
});
