// Sources > App: the Juk3box Helper iPhone app (jb-source-helper). Phones
// pair from the app itself, with a code on the cabinet; here: which may
// pair, each phone (rename, sync, forget), waking the app over Bluetooth,
// and the jukebox's name in the app and the AirPlay speaker list.

import { get, post } from '../api.js';
import { button, clear, confirmDialog, emptyState, field, h, icon, modal, pageHeader, showError, switchControl, toast } from '../ui.js';

let timer = null;
let body = null;
let lastJson = '';

export async function show(main) {
  body = h('div.stack');
  lastJson = '';
  clear(main, pageHeader('App', 'Phones that play their Apple Music library through the Juk3box Helper app, sent to the cabinet over AirPlay. Beside Bluetooth, not instead of it.'), body);
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
    data = await get('/api/sources');
  } catch (e) {
    clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  // Only on a change: a re-render would close an open rename box's focus.
  const json = JSON.stringify(data);
  if (body?.isConnected && json !== lastJson) {
    lastJson = json;
    render(data);
  }
}

async function act(action, payload, done) {
  try {
    await post(`/api/helper/${action}`, payload);
    if (done) toast(done, 'ok');
    lastJson = '';
    await refresh();
  } catch (e) {
    showError(e);
  }
}

function when(t) {
  const d = new Date(t * 1000);
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
}

function rename(title, current, onSave) {
  const input = h('input', { type: 'text', value: current || '', maxLength: 40, required: true });
  modal({
    title,
    body: field('Name', input),
    actions: [
      { label: 'Cancel' },
      { label: 'Save', variant: 'primary', onclick: () => { if (!input.value.trim()) return false; onSave(input.value.trim()); } },
    ],
  });
  input.select();
}

function phoneRow(p, data) {
  const id = `helper:${p.device_id}`;
  const active = data.source?.active?.id === id;
  const status = [
    active ? 'Has the jukebox' : p.reachable ? 'Here' : 'Not here',
    p.awake ? 'app open' : p.reachable ? 'app asleep, woken for a pick' : p.last_seen ? `last seen ${when(p.last_seen)}` : null,
    p.songs ? `${p.songs} songs${p.synced_at ? `, read ${when(p.synced_at)}` : ''}` : 'library not read yet',
  ].filter(Boolean).join(' · ');
  const name = p.name || 'iPhone';
  return h('div.card.card-pad.phone-row',
    h('div.stat-icon', icon('smartphone')),
    h('div.phone-text', h('div.stat-value', name), h('div.stat-sub', status)),
    h('div.phone-actions',
      active ? null : button('Use', { variant: p.reachable ? 'primary' : '', onclick: async () => {
        try { await post('/api/sources/use', { id }); toast(`Switched to ${name}.`, 'ok'); } catch (e) { showError(e); }
      } }),
      active && p.reachable ? button('Sync now', { onclick: () => act('sync', {}, `Reading ${name}'s library again…`) }, 'refresh-cw') : null,
      button('Rename', { onclick: () => rename(`Rename ${name}`, name, n => act('rename', { device_id: p.device_id, name: n }, `Renamed to ${n}.`)) }, 'pencil'),
      button('Forget', {
        variant: 'danger',
        onclick: async () => {
          const ok = await confirmDialog(`Forget ${name}?`,
            'The app is unpaired, and its songs, playlists and plays are removed from the jukebox. Pair it again from the app (turn on "Allow new phones to pair" first).',
            { confirm: 'Forget', danger: true });
          if (ok) act('forget', { device_id: p.device_id }, `Forgot ${name}.`);
        },
      }, 'trash-2')));
}

function switchRow(iconName, title, sub, on, onChange) {
  return h('div.card.card-pad.phone-row',
    h('div.stat-icon', icon(iconName)),
    h('div.phone-text', h('div.stat-value', title), h('div.stat-sub', sub)),
    switchControl(on, onChange, on ? 'On' : 'Off'));
}

function render(data) {
  const s = data.helper_settings || {};
  const phones = data.helper?.phones || [];
  const name = s.name || 'Juk3box';
  clear(body,
    data.bus ? null : h('div.notice.warn', icon('circle-alert'),
      h('span', 'The admin can\'t reach the jukebox right now, so this may be out of date.')),
    h('h2', 'Phones'),
    phones.length ? h('div.stack', phones.map(p => phoneRow(p, data)))
      : emptyState('smartphone', 'No phones yet', 'Install Juk3box Helper on an iPhone and open it on the same Wi-Fi: a code shows on the cabinet to type in the app.'),
    h('h2', 'Settings'),
    switchRow('lock', 'Allow new phones to pair',
      s.allow_pairing ? 'On: an app that finds the jukebox shows a pairing code on the cabinet. It turns off by itself once a phone pairs.'
        : 'Off: only phones already paired can connect. Strangers\' apps are told pairing is off, and nothing shows on the cabinet.',
      !!s.allow_pairing, v => act('settings', { allow_pairing: v }, v ? 'New phones can pair (until one does).' : 'Pairing is off.')),
    switchRow('bluetooth', 'Wake the app over Bluetooth',
      s.ble !== false ? 'On: a pick wakes the app when iOS has put it to sleep, so the phone can stay in a pocket.'
        : 'Off: picks play only while the app is open on the phone.',
      s.ble !== false, v => act('settings', { ble: v }, v ? 'Bluetooth wake-up is on.' : 'Bluetooth wake-up is off.')),
    h('div.card.card-pad.phone-row',
      h('div.stat-icon', icon('radio')),
      h('div.phone-text', h('div.stat-value', name), h('div.stat-sub', 'The jukebox\'s name in the app and in the phone\'s AirPlay speaker list.')),
      h('div.phone-actions', button('Rename', {
        onclick: () => rename('Rename the jukebox', name, n => act('settings', { name: n }, `The jukebox is now ${n}.`)),
      }, 'pencil'))));
}
