// JUKE.BOX UI engine: loads the active theme and builds the screen from
// its layouts. Behavior lives here; the look lives in the theme.

import { connectBus } from './bus.js';
import { renderLayout } from './layout.js';
import { store } from './store.js';
import { fetchTheme, applyTheme } from './theme.js';

const state = {
  theme: null,
  screen: null,
};

function context() {
  return { base: state.theme.base, blocks: state.theme.blocks };
}

function showScreen(name) {
  const layout = state.theme.layouts[name];
  const holder = document.querySelector('jb-screen');
  if (!layout || !holder) return;
  holder.replaceChildren(renderLayout(layout, context()));
  state.screen = name;
  document.documentElement.dataset.screen = name;
}

async function start() {
  state.theme = await fetchTheme();
  await applyTheme(state.theme);
  const html = document.documentElement;
  store.watch(html, 'phone', p => { html.dataset.phone = p?.connected ? 'connected' : 'disconnected'; });
  document.getElementById('jb-root').replaceChildren(renderLayout(state.theme.layouts.frame, context()));
  showScreen('home');
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
