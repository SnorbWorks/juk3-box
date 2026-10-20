// Sources > Bluetooth: on or off, the phones paired with the jukebox, which
// one has it, and pairing a new one. The same as the cabinet's Settings >
// Bluetooth; jb-source-phone does the work. One phone has the jukebox at a
// time, Bluetooth or the app (jb-core chooses; Connect asks it).

import { get, post } from '../api.js';
import { button, clear, confirmDialog, emptyState, h, icon, pageHeader, showError, switchControl, toast } from '../ui.js';

let timer = null;
let body = null;

export async function show(main) {
  body = h('div.stack');
  clear(main, pageHeader('Bluetooth', 'Phones paired over Bluetooth. One phone has the jukebox at a time (Bluetooth or the app); switch here or with the cabinet\'s Sources button.'), body);
  await refresh();
  timer = setInterval(refresh, 1500);
}

export function leave() {
  clearInterval(timer);
  timer = null;
}

async function refresh() {
  let data;
  try {
    data = await get('/api/phones');
  } catch (e) {
    clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  if (body?.isConnected) render(data);
}

let activeId = null;

async function act(action, payload = {}, done) {
  try {
    await post(action === 'use' ? '/api/sources/use' : `/api/phones/${action}`, payload);
    if (done) toast(done, 'ok');
    await refresh();
  } catch (e) {
    showError(e);
  }
}

function statusOf(p) {
  if (p.mac === activeId) return p.connected ? 'Has the jukebox' : 'Has the jukebox, connecting';
  if (p.connected) return p.active ? 'Connected (the app has the jukebox)' : 'Connected, leaving';
  if (!p.last_connected) return 'Not connected yet';
  const when = new Date(p.last_connected * 1000);
  return `Last used ${when.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${when.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
}

function phoneRow(p) {
  const actions = [
    p.mac !== activeId
      ? button(p.connected ? 'Use' : 'Connect', { variant: 'primary', onclick: () => act('use', { id: p.mac }, p.connected ? `Switched to ${p.name}.` : `Connecting to ${p.name}…`) }, 'bluetooth')
      : null,
    p.connected
      ? button('Disconnect', { onclick: () => act('disconnect', { mac: p.mac }, `Disconnected ${p.name}.`) }, 'x')
      : null,
    button('Forget', {
      variant: 'danger',
      onclick: async () => {
        const ok = await confirmDialog(`Forget ${p.name}?`,
          'It\'s unpaired, and its songs, playlists and plays are removed from the jukebox. It can be paired again later.',
          { confirm: 'Forget', danger: true });
        if (ok) act('forget', { mac: p.mac }, `Forgot ${p.name}.`);
      },
    }, 'trash-2'),
  ];
  return h('div.card.card-pad.phone-row',
    h('div.stat-icon', icon('smartphone')),
    h('div.phone-text', h('div.stat-value', p.name), h('div.stat-sub', statusOf(p))),
    h('div.phone-actions', actions));
}

function pairingCard(s) {
  const head = h('div.card-head', h('h2', 'Pair a new phone'));
  let content;
  if (s.state === 'waiting') {
    const left = Math.max(0, Math.round((s.until || 0) - Date.now() / 1000));
    content = [
      h('p', 'Pairing is on. On the phone, open ', h('b', 'Settings → Bluetooth'), ' and tap ', h('b', s.jukebox || 'the jukebox'), '.'),
      h('p.muted', `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left`),
      button('Cancel', { onclick: () => act('pair_stop') }),
    ];
  } else if (s.state === 'confirm') {
    content = [
      h('p', 'Does ', h('b', s.name || 'the phone'), ' show this code?'),
      h('div.pair-code', s.code ? `${s.code.slice(0, 3)} ${s.code.slice(3)}` : '—'),
      h('div.quick',
        button('Pair', { variant: 'primary', onclick: () => act('pair_confirm', { ok: true }) }, 'check'),
        button('Reject', { onclick: () => act('pair_confirm', { ok: false }) }, 'x')),
    ];
  } else if (s.state === 'paired') {
    content = [h('div.notice.ok', icon('check'), h('span', `Paired with ${s.name}. It has the jukebox now.`))];
  } else if (s.state === 'failed') {
    content = [h('div.notice.warn', icon('circle-alert'), h('span', 'Pairing didn\'t finish. Try again.')),
      button('Pair a new phone', { variant: 'primary', onclick: () => act('pair_start') }, 'bluetooth')];
  } else {
    content = [
      h('p.muted', 'Makes the jukebox visible to phones for two minutes. The pairing goes through once you confirm the code here or on the cabinet.'),
      button('Pair a new phone', { variant: 'primary', onclick: () => act('pair_start') }, 'bluetooth'),
    ];
  }
  return h('div.card', head, h('div.card-pad.stack', content));
}

function powerCard(on, phones) {
  const toggle = switchControl(on, async value => {
    const connected = phones.find(p => p.connected);
    if (!value && connected && !await confirmDialog('Turn Bluetooth off?',
      `${connected.name} disconnects and the music stops. No phone can connect until it's back on.`,
      { confirm: 'Turn off', danger: true })) {
      toggle.querySelector('input').checked = true;
      return;
    }
    act('bluetooth', { on: value }, value ? 'Bluetooth is on.' : 'Bluetooth is off.');
  }, on ? 'On' : 'Off');
  return h('div.card.card-pad.phone-row',
    h('div.stat-icon', icon('bluetooth')),
    h('div.phone-text', h('div.stat-value', 'Bluetooth'),
      h('div.stat-sub', on ? 'Phones can connect and pair.' : 'Off: the jukebox is invisible and no phone can connect.')),
    toggle);
}

function render(data) {
  const on = data.bluetooth?.on !== false;
  activeId = data.source?.active?.id || null;
  clear(body,
    data.bus ? null : h('div.notice.warn', icon('circle-alert'),
      h('span', 'The admin can\'t reach the jukebox right now, so this may be out of date.')),
    powerCard(on, data.phones),
    !on ? null : data.phones.length
      ? h('div.stack', data.phones.map(phoneRow))
      : emptyState('smartphone', 'No phones paired yet', 'Pair one below.'),
    on ? pairingCard(data.pairing || { state: 'idle' }) : null);
}
