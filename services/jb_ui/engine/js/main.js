// JUKE.BOX UI engine: loads the active theme and builds the screen from
// its layouts. Behavior lives here; the look lives in the theme.

import { connectBus, publish } from './bus.js';
import { click, currentMode, enterMode, goToRest, refocus, rotate } from './focus.js';
import { renderLayout } from './layout.js';
import { closePopup, initPopups, openPopup, popupOpen, updatePopup } from './popup.js';
import { store } from './store.js';
import { fetchTheme, applyTheme } from './theme.js';

const VOLUME_STEP = 0.05;   // per knob detent
const NOTICE_MS = 4000;

let theme = null;
const html = document.documentElement;

const actions = {
  nav(name) {
    if (name === 'volume') volumeMode();
    else if (name === 'shuffle') toggleShuffle();
    else showScreen(name);
  },
};

function context() {
  return { base: theme.base, blocks: theme.blocks, icons: theme.icons, settings: theme.settings, actions };
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
  initPopups(theme.blocks);
  watchSystem();
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
