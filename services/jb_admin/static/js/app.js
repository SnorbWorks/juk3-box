// The admin app: login (or first-time setup), then the shell -- a sidebar
// of pages like WordPress's dashboard -- and a hash router (#/builder).

import { ApiError, get, post, whenLoggedOut } from './api.js';
import { button, clear, field, h, icon, loadIcons, showError, toast } from './ui.js';

const PAGES = [
  { id: 'dashboard', label: 'Dashboard', icon: 'gauge', load: () => import('./pages/dashboard.js') },
  { id: 'phones', label: 'Bluetooth', icon: 'bluetooth', load: () => import('./pages/phones.js') },
  { id: 'sound', label: 'Sound', icon: 'volume-2', load: () => import('./pages/sound.js') },
  { id: 'drives', label: 'Drives', icon: 'usb', load: () => import('./pages/drives.js') },
  { id: 'builder', label: 'Page Builder', icon: 'layout-template', load: () => import('./pages/builder.js') },
  { id: 'themes', label: 'Themes', icon: 'palette', load: () => import('./pages/themes.js') },
  { id: 'appearance', label: 'Appearance', icon: 'swatch-book', load: () => import('./pages/appearance.js') },
  { id: 'media', label: 'Media', icon: 'image', load: () => import('./pages/media.js') },
  { id: 'settings', label: 'Settings', icon: 'settings', load: () => import('./pages/settings.js') },
  { id: 'modules', label: 'Modules', icon: 'boxes', load: () => import('./pages/modules.js') },
  { id: 'extensions', label: 'Extensions', icon: 'scroll-text', load: () => import('./pages/extensions.js') },
  { id: 'account', label: 'Account', icon: 'user', load: () => import('./pages/account.js') },
];

const app = document.getElementById('app');
let current = null;     // the page module showing, so it can clean up

async function start() {
  await loadIcons();
  whenLoggedOut(() => { if (!app.classList.contains('login')) showLogin(false); });
  let session;
  try {
    session = await get('/api/session');
  } catch (e) {
    clear(app, h('div.boot-mark', 'JUKE.BOX'), h('p.boot-error', e.message));
    app.className = 'boot';
    setTimeout(start, 4000);
    return;
  }
  if (session.logged_in) showShell();
  else showLogin(session.setup_needed);
}

// Login and first-time setup ------------------------------------------------

function showLogin(setup) {
  current?.leave?.();
  current = null;
  app.className = 'login';
  const password = h('input', { type: 'password', autocomplete: setup ? 'new-password' : 'current-password', required: true, minLength: setup ? 8 : undefined });
  const again = setup ? h('input', { type: 'password', autocomplete: 'new-password', required: true }) : null;
  const message = h('p.form-error', { hidden: true });
  const submit = button(setup ? 'Create password' : 'Log in', { variant: 'primary wide', type: 'submit' });
  const form = h('form.login-card', {
    onsubmit: async e => {
      e.preventDefault();
      message.hidden = true;
      if (setup && password.value !== again.value) {
        message.textContent = 'The two passwords don\'t match.';
        message.hidden = false;
        return;
      }
      submit.disabled = true;
      try {
        await post(setup ? '/api/setup' : '/api/login', { password: password.value });
        showShell();
      } catch (err) {
        message.textContent = err.message;
        message.hidden = false;
        submit.disabled = false;
        password.select();
      }
    },
  },
  h('div.login-brand', h('div.brand-mark', icon('radio')), h('div', h('div.brand-name', 'JUKE.BOX'), h('div.brand-sub', 'Admin'))),
  setup ? h('p.login-lead', 'Welcome! Choose a password for the admin. You\'ll use it from any phone or computer on your network.') : null,
  field(setup ? 'New password' : 'Password', password, setup ? 'At least 8 characters.' : null),
  setup ? field('Type it again', again) : null,
  message,
  submit);
  clear(app, form);
  password.focus();
}

// The shell -------------------------------------------------------------------

function showShell() {
  app.className = 'shell';
  const nav = h('nav.side-nav', { 'aria-label': 'Admin pages' },
    PAGES.map(p => h('a.side-link', { href: `#/${p.id}`, dataset: { page: p.id } }, icon(p.icon), h('span', p.label))));
  const logout = h('button.side-link.logout', {
    type: 'button',
    onclick: async () => {
      try { await post('/api/logout'); } catch { /* logged out anyway */ }
      showLogin(false);
    },
  }, icon('log-out'), h('span', 'Log out'));
  const sidebar = h('aside.sidebar',
    h('div.side-brand', h('div.brand-mark.small', icon('radio')), h('span.brand-name', 'JUKE.BOX')),
    nav, h('div.side-foot', logout));
  const scrim = h('div.scrim', { onclick: () => app.classList.remove('nav-open') });
  const topbar = h('header.topbar',
    h('button.icon-btn.menu-btn', { type: 'button', 'aria-label': 'Menu', onclick: () => app.classList.toggle('nav-open') }, icon('menu')),
    h('span.topbar-title', 'JUKE.BOX'));
  const main = h('main.content', { id: 'content' });
  clear(app, sidebar, scrim, h('div.main-col', topbar, main));
  route();
}

async function route() {
  if (!app.classList.contains('shell')) return;
  const [, id = 'dashboard', ...rest] = location.hash.split('/');
  const page = PAGES.find(p => p.id === id) || PAGES[0];
  for (const a of document.querySelectorAll('.side-link[data-page]')) a.toggleAttribute('aria-current', a.dataset.page === page.id);
  app.classList.remove('nav-open');
  app.dataset.page = page.id;
  document.title = `${page.label} · JUKE.BOX Admin`;

  if (current?.leave?.() === false) return;   // e.g. unsaved changes kept the page
  const main = document.getElementById('content');
  clear(main, h('div.loading', h('span.spinner'), 'Loading…'));
  try {
    const mod = await page.load();
    current = mod;
    await mod.show(main, rest.map(decodeURIComponent));
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return;
    console.error(e);
    clear(main, h('div.empty', icon('circle-alert', 'big'), h('h3', 'This page didn\'t load'), h('p', e.message),
      button('Try again', { onclick: route }, 'refresh-cw')));
  }
}

addEventListener('hashchange', route);
addEventListener('unhandledrejection', e => { if (e.reason instanceof ApiError) { showError(e.reason); e.preventDefault(); } });

export function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

export { toast };

start();
