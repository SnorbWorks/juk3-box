// Sources > Apple Music: Apple Music itself (its genres and charts) on the
// cabinet, through the Juk3box Helper app and the phone's subscription
// (docs/contracts/apple-music.md, "The admin"). Here: whether it's on and
// available, the shortcuts to where it shows (the navigation bar, the
// Browse row, a Home row of its own), and what it shows (clean versions,
// how often lists are fetched again, which genres in what order).

import { get, post } from '../api.js';
import { button, clear, field, h, icon, pageHeader, showError, switchControl, toast } from '../ui.js';
import { sortable } from '../sortable.js';

const REASONS = {
  off: 'Off. Turn it on below to browse Apple Music on the cabinet.',
  no_phone: 'Connect your phone: Apple Music comes through the Juk3box Helper app on the phone that has the jukebox.',
  app_update: 'Update the Juk3box Helper app on the phone: this version can\'t browse Apple Music.',
  no_subscription: 'This phone has no Apple Music subscription, which browsing Apple Music needs.',
};
const REFRESH = [[6, '6 hours'], [24, '24 hours'], [168, 'Week']];

let timer = null;
let body = null;
let lastJson = '';
let asked = false;     // the genres asked for once, if the jukebox has none yet

export async function show(main) {
  body = h('div.stack');
  lastJson = '';
  asked = false;
  clear(main, pageHeader('Apple Music', 'Browse Apple Music itself on the cabinet: its genres and charts, with their cover art. It comes through the Juk3box Helper app and the phone\'s Apple Music subscription; songs picked there play like your own.'), body);
  await refresh();
  timer = setInterval(refresh, 2000);
}

export function leave() {
  clearInterval(timer);
  timer = null;
}

async function refresh() {
  let data;
  try {
    data = await get('/api/apple-music');
  } catch (e) {
    clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  // Only on a change, and never mid-drag: a re-render would drop the genre being moved.
  const json = JSON.stringify(data);
  if (body?.isConnected && json !== lastJson && !body.querySelector('.dragging')) {
    lastJson = json;
    render(data);
  }
  if (!asked && data.settings.on && data.status?.available && !data.genres?.all) {
    asked = true;
    post('/api/apple-music/load', {}).catch(() => {});
  }
}

async function act(action, payload, done) {
  try {
    const res = await post(`/api/apple-music/${action}`, payload);
    if (done) toast(done, 'ok');
    lastJson = '';
    await refresh();
    return res;
  } catch (e) {
    showError(e);
    lastJson = '';
    await refresh();
    return null;
  }
}

function statusCard(data) {
  const s = data.status;
  const p = data.phone;
  const sub = [
    p ? `${p.name || 'iPhone'}${p.connected ? '' : ' (not here)'}` : 'No app phone has the jukebox',
    p?.music?.canPlayCatalog === true ? 'Apple Music subscription' : p?.music?.canPlayCatalog === false ? 'no subscription' : null,
    s?.storefront ? `store: ${s.storefront.toUpperCase()}` : null,
  ].filter(Boolean).join(' · ');
  const available = !!s?.available;
  return h('div.card.card-pad.phone-row',
    h('div.stat-icon', icon('music')),
    h('div.phone-text',
      h('div.stat-value', available ? 'Available' : s?.on ? 'Not available' : 'Off'),
      h('div.stat-sub', available ? sub : (REASONS[s?.reason] || 'Waiting for the jukebox to say.')),
      available ? null : h('div.stat-sub.muted', sub)),
    h(`span.badge.${available ? 'ok' : s?.on ? 'warn' : ''}`, available ? 'Ready' : s?.on ? 'Waiting' : 'Off'));
}

function onCard(data) {
  const on = !!data.settings.on;
  const toggle = switchControl(on, v => act('settings', { on: v },
    v ? 'Apple Music is on: it shows on the cabinet wherever it\'s added.' : 'Apple Music is off: it\'s hidden on the cabinet.'),
  on ? 'On' : 'Off');
  return h('div.card.card-pad.phone-row',
    h('div.stat-icon', icon('power')),
    h('div.phone-text', h('div.stat-value', 'Apple Music on the jukebox'),
      h('div.stat-sub', on ? 'On: the cabinet shows Apple Music wherever it\'s added below.'
        : 'Off: every Apple Music button, card and row is hidden on the cabinet, so a layout that has them still looks right.')),
    toggle);
}

// One place Apple Music can show: added (with a link to where it lives), or a button that adds it.
function placeRow(iconName, title, sub, state, addLabel, onAdd, link) {
  const added = state?.added;
  return h('div.card.card-pad.phone-row',
    h('div.stat-icon', icon(iconName)),
    h('div.phone-text', h('div.stat-value', title), h('div.stat-sub', sub)),
    h('div.phone-actions',
      added ? h('span.badge.ok', icon('check'), 'Added') : null,
      added || !onAdd ? (link ? h('a.btn', { href: link[1] }, h('span.btn-label', link[0])) : null)
        : button(addLabel, { variant: 'primary', disabled: state && !state.can_add, onclick: onAdd }, 'plus')));
}

function placesSection(data) {
  const s = data.shortcuts;
  const builder = '#/builder/home';
  return [
    h('h2', 'Where it shows'),
    h('div.stack',
      placeRow('navigation', 'Navigation button', s.nav.can_add || s.nav.added
        ? 'A button in the cabinet\'s navigation bar that opens the Apple Music screen. Its place is set in Menus › Navigation.'
        : 'This jukebox\'s screen doesn\'t have the Apple Music button yet: update the jukebox first.',
      s.nav, 'Add to navigation', () => act('shortcut', { what: 'nav' }, 'Added to the navigation bar.'), ['Edit in Menus', '#/menus']),
      placeRow('layout-grid', 'Card in the Browse row', s.browse.can_add || s.browse.added
        ? 'An Apple Music card beside Artists, Albums and the rest on Home.'
        : 'Home has no Browse row in this theme. Add one in the Page builder, then come back.',
      s.browse, 'Add to Browse', () => act('shortcut', { what: 'browse' }, 'Added to the Browse row.'), ['Open the Page builder', builder]),
      placeRow('rows-3', 'A Home row of its own', 'A row of genre tiles on Home, after the Browse row, with See All.',
        s.home_row, 'Add a Home row', () => act('shortcut', { what: 'home_row' }, 'Added a Home row.'), ['Open the Page builder', builder]),
      placeRow('square-plus', 'Apple Music block', 'Put it anywhere with the Page builder: a tile, a row or a grid of genres or playlists, fitting its space.',
        null, null, null, ['Open the Page builder', builder])),
  ];
}

// Every genre, ticked to show, dragged to order. Nothing chosen yet
// (genres null) is every genre in Apple's order, all ticked.
function genresField(data) {
  const all = data.genres?.all || [];
  if (!all.length) {
    const why = data.genres?.state === 'error' ? 'They didn\'t load from the phone' : data.genres?.state === 'loading' ? 'Loading them from the phone…'
      : !data.settings.on ? 'They load from the phone once Apple Music is on' : 'They load from the phone when it\'s available';
    return field('Genres', h('p.muted', `${why}.`));
  }
  const byId = Object.fromEntries(all.map(g => [g.id, g]));
  const chosen = data.settings.genres;
  const on = new Set(chosen === null ? all.map(g => g.id) : chosen.filter(id => id in byId));
  const order = chosen === null ? all.map(g => g.id)
    : [...chosen.filter(id => id in byId), ...all.map(g => g.id).filter(id => !on.has(id))];
  const list = h('ul.order-list.am-genres');
  const save = () => {
    const ticked = order.filter(id => on.has(id));
    if (!ticked.length) { toast('Keep at least one genre.', 'error'); return false; }
    // Every genre in Apple's order: back to "all", so genres Apple adds later show too.
    const asApple = ticked.length === all.length && ticked.every((id, i) => id === all[i].id);
    act('settings', { genres: asApple ? null : ticked });
    return true;
  };
  const draw = () => clear(list, order.map(id => {
    const g = byId[id];
    const box = h('input', { type: 'checkbox', checked: on.has(id), 'aria-label': `Show ${g.name}`, onchange: () => {
      if (box.checked) on.add(id); else on.delete(id);
      if (!save()) { on.add(id); box.checked = true; }
      draw();
    } });
    return h(`li.order-item${on.has(id) ? '' : '.off'}`,
      h('span.icon-btn', { dataset: { handle: '' }, style: { cursor: 'grab', touchAction: 'none' }, title: 'Drag to reorder' }, icon('grip-vertical')),
      box,
      g.cover ? h('img.am-cover', { src: g.cover, alt: '', loading: 'lazy' }) : h('span.am-cover'),
      h('span.order-label', g.name || id));
  }));
  sortable(list, (from, to) => {
    const [item] = order.splice(from, 1);
    order.splice(to, 0, item);
    draw();
    save();
  });
  draw();
  return field('Genres', h('div', list,
    chosen === null ? null : h('p', button('Every genre, in Apple\'s order', { onclick: () => act('settings', { genres: null }, 'Showing every genre.') }, 'rotate-ccw'))),
  'Tick the genres to show, and drag them into the order you want. Each tile shows its genre\'s top playlist\'s cover.');
}

function contentSection(data) {
  const s = data.settings;
  const refresh = h('div.segmented', REFRESH.map(([hours, label]) => h('button', {
    type: 'button', 'aria-pressed': String(s.refresh_hours === hours),
    onclick: () => act('settings', { refresh_hours: hours }, `Lists are fetched again every ${label === 'Week' ? 'week' : label}.`),
  }, label)));
  return [
    h('h2', 'Content'),
    h('div.card.card-pad.stack',
      h('div.phone-row',
        h('div.phone-text', h('div.stat-value', 'Clean versions only'),
          h('div.stat-sub', s.clean ? 'On: explicit songs and albums are hidden everywhere Apple Music shows.'
            : 'Off: explicit songs show, the same as on the phone.')),
        switchControl(!!s.clean, v => act('settings', { clean: v }, v ? 'Explicit songs are hidden.' : 'Explicit songs show.'), s.clean ? 'On' : 'Off')),
      field('Refresh lists every', h('div.phone-row', refresh,
        button('Refresh now', { disabled: !data.status?.available, onclick: () => act('refresh', {}, 'Fetching the lists again…') }, 'refresh-cw')),
      'How long the genres and charts are kept before they\'re fetched from the phone again.'),
      genresField(data)),
  ];
}

function render(data) {
  clear(body,
    data.bus ? null : h('div.notice.warn', icon('circle-alert'),
      h('span', 'The admin can\'t reach the jukebox right now, so this may be out of date.')),
    statusCard(data),
    onCard(data),
    placesSection(data),
    contentSection(data),
    h('h2', 'The Apple Music screen'),
    h('div.card.card-pad.phone-row',
      h('div.stat-icon', icon('layout-template')),
      h('div.phone-text', h('div.stat-value', 'Its own page'),
        h('div.stat-sub', 'Top playlists, then the genres. Rearrange or restyle it like Home.')),
      h('a.btn', { href: '#/builder/apple-music' }, icon('external-link'), h('span.btn-label', 'Open in the Page builder'))));
}
