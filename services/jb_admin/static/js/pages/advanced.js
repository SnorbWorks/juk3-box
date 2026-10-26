// Advanced: the settings an owner rarely needs, kept off the everyday
// pages: the system drive's protection and remote access for a developer.
// (Their state comes with the Updates tab's, /api/updates.)

import { get, post } from '../api.js';
import { button, clear, h, icon, pageHeader, showError, switchControl, toast } from '../ui.js';

const WRAP = { whiteSpace: 'normal', overflow: 'visible' };   // these say more than one line's worth

export async function show(main) {
  let u;
  try { u = await get('/api/updates'); } catch (e) { showError(e); return; }
  const refresh = () => show(main);
  clear(main,
    pageHeader('Advanced', 'Settings you\'ll rarely need.'),
    h('div.stack', { style: { maxWidth: '720px' } },
      rootfsCard(u.rootfs, refresh),
      sshCard(u.ssh, refresh)));
}

function row(iconName, title, sub, ...actions) {
  return h('div.phone-row',
    h('div.stat-icon', icon(iconName)),
    h('div.phone-text', h('div.stat-value', title), sub ? h('div.stat-sub', { style: WRAP }, sub) : null),
    actions.some(Boolean) ? h('div.phone-actions', actions) : null);
}

function card(...children) {
  return h('div.card.card-pad', children);
}

// The read-only system drive (deploy/jb-rootfs).
function rootfsCard(r, refresh) {
  if (!r.data) {
    return card(row('hard-drive', 'System drive',
      'Not protected. A card made from the Juk3box image keeps its system read-only, so a pulled plug can\'t damage it; this one was set up another way.'));
  }
  let line;
  if (r.protected && r.next_start) line = 'Protected: the system is read-only, so a pulled plug can\'t damage it. Your songs, settings and phones are kept on their own part of the card. Updates restart the jukebox to install, then once more.';
  else if (r.next_start) line = 'Protected from the next start.';
  else if (r.protected) line = 'Protected until the next start, then writable.';
  else line = 'Off: the system drive is writable, so a pulled plug could damage it.';
  const flip = async on => {
    try {
      await post(`/api/updates/rootfs/${on ? 'on' : 'off'}`, {});
      toast(on ? 'The system drive is protected from the next start.' : 'The system drive is writable from the next start.', 'success');
      refresh();
    } catch (e) { showError(e); }
  };
  return card(
    h('div.phone-row',
      h('div.stat-icon', icon('hard-drive')),
      h('div.phone-text', h('div.stat-value', 'System drive'), h('div.stat-sub', { style: WRAP }, line)),
      switchControl(r.next_start, flip, r.next_start ? 'On' : 'Off')));
}

// SSH for a developer: off on a card from the image (deploy/install/seal.sh).
function sshCard(s, refresh) {
  if (!s) return null;
  const key = h('textarea', { rows: 2, placeholder: 'ssh-ed25519 AAAA… you@laptop', 'aria-label': 'Your public key',
    style: { width: '100%', fontFamily: 'monospace', fontSize: '13px' } });
  const send = async on => {
    try {
      await post('/api/updates/ssh', { on, key: key.value.trim() || null });
      toast(on ? 'SSH is on.' : 'SSH is off.', 'success');
      refresh();
    } catch (e) { showError(e); refresh(); }
  };
  const line = s.on
    ? `On: ${s.keys} key${s.keys === 1 ? '' : 's'} can log in as admin. Add another below.`
    : 'Off. For a developer looking into a problem: paste your public key, then turn it on.';
  return card(
    h('div.phone-row',
      h('div.stat-icon', icon('terminal')),
      h('div.phone-text', h('div.stat-value', 'Remote access (SSH)'), h('div.stat-sub', { style: WRAP }, line)),
      switchControl(s.on, send, s.on ? 'On' : 'Off')),
    h('div', { style: { marginTop: '10px' } }, key,
      s.on ? button('Add the key', { variant: 'small', onclick: () => send(true) }) : null));
}
