// Sources: where the music comes from. The phone that has the jukebox
// (Bluetooth or the juk3box Helper app; one at a time, jb-core chooses,
// like the cabinet's Sources button) and the three kinds, each with its own
// settings page: #/sources/bluetooth, #/sources/app, #/sources/drives.

import { get, post } from '../api.js';
import { button, clear, h, icon, pageHeader, showError, toast } from '../ui.js';

const KINDS = {
  bluetooth: { label: 'Bluetooth', icon: 'bluetooth', load: () => import('./phones.js') },
  app: { label: 'App', icon: 'smartphone', load: () => import('./helper-app.js') },
  drives: { label: 'Drives', icon: 'usb', load: () => import('./drives.js') },
};

let sub = null;       // the open kind's page module
let timer = null;
let body = null;

export async function show(main, [kind, ...rest] = []) {
  if (KINDS[kind]) {
    sub = await KINDS[kind].load();
    const inner = h('div');
    clear(main, h('a.back-crumb', { href: '#/sources' }, icon('chevron-left'), 'Sources'), inner);
    document.title = `${KINDS[kind].label} · Sources · JUKE.BOX Admin`;
    await sub.show(inner, rest);
    return;
  }
  sub = null;
  body = h('div.stack');
  clear(main, pageHeader('Sources', 'Where the music comes from. One phone has the jukebox at a time, over Bluetooth or the juk3box Helper app; drives\' songs are always on the board.'), body);
  await refresh();
  timer = setInterval(refresh, 2000);
}

export function leave() {
  clearInterval(timer);
  timer = null;
  const s = sub;
  sub = null;
  return s?.leave?.();
}

async function refresh() {
  let data;
  try {
    data = await get('/api/sources');
  } catch (e) {
    clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  if (body?.isConnected) render(data);
}

async function use(p) {
  try {
    await post('/api/sources/use', { id: p.id });
    toast(p.connected ? `Switched to ${p.name}.` : `Switching to ${p.name}…`, 'ok');
    await refresh();
  } catch (e) {
    showError(e);
  }
}

// Every phone that could have the jukebox, the one that has it first.
function phonesOf(data) {
  const activeId = data.source?.active?.id;
  const list = [];
  if (data.bluetooth?.on !== false) {
    for (const p of data.phones) list.push({ id: p.mac, kind: 'Bluetooth', name: p.name, connected: p.connected });
  }
  for (const p of data.helper?.phones || []) {
    list.push({ id: `helper:${p.device_id}`, kind: 'App', name: p.name || 'iPhone', connected: !!p.reachable });
  }
  return list.map(p => ({ ...p, active: p.id === activeId })).sort((a, b) => b.active - a.active);
}

function nowCard(data) {
  const a = data.source?.active;
  const offer = data.source?.offer;
  const phones = phonesOf(data);
  const rows = phones.map(p => h('div.phone-row.source-phone',
    h('div.phone-text',
      h('div.stat-value', p.name),
      h('div.stat-sub', `${p.kind} · ${p.active ? (p.connected ? 'Has the jukebox' : 'Has the jukebox, not here') : p.connected ? 'Here' : 'Not here'}`)),
    p.active ? h('span.badge.ok', 'In use') : button('Use', { variant: p.connected ? 'primary' : '', onclick: () => use(p) })));
  return h('div.card',
    h('div.card-head', h('h2', 'Has the jukebox')),
    h('div.card-pad.stack',
      a ? null : h('p.muted', 'No phone yet. The first one to connect gets it.'),
      offer ? h('div.notice', icon('info'), h('span', `${offer.name} is here too; the cabinet is asking whether to switch.`)) : null,
      rows.length ? rows : h('p.muted', 'No phones paired. Pair one over Bluetooth, or from the juk3box Helper app.')));
}

function kindLink(id, sub) {
  const k = KINDS[id];
  return h('a.card.card-pad.phone-row.source-link', { href: `#/sources/${id}` },
    h('div.stat-icon', icon(k.icon)),
    h('div.phone-text', h('div.stat-value', k.label), h('div.stat-sub', sub)),
    icon('chevron-right'));
}

function render(data) {
  const bt = data.bluetooth?.on !== false;
  const btConnected = data.phones.find(p => p.connected);
  const apps = data.helper?.phones || [];
  const pairing = data.helper_settings?.allow_pairing;
  const drives = data.drives || [];
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  clear(body,
    data.bus ? null : h('div.notice.warn', icon('circle-alert'),
      h('span', 'The admin can\'t reach the jukebox right now, so this may be out of date.')),
    nowCard(data),
    h('div.stack',
      kindLink('bluetooth', !bt ? 'Off' : `${plural(data.phones.length, 'phone')} paired${btConnected ? ` · ${btConnected.name} connected` : ''}`),
      kindLink('app', `${apps.length ? plural(apps.length, 'phone') : 'No phones'} · new phones ${pairing ? 'can pair' : 'can\'t pair'}`),
      kindLink('drives', drives.length ? `${drives.map(d => d.label || 'USB drive').join(', ')} plugged in` : 'None plugged in')));
}
