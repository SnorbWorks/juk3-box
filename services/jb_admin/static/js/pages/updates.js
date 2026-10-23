// Updates: everything on the jukebox that updates, in one place
// (services/jb_admin/updates.py): Juk3box, Raspberry Pi OS, the keypad
// board, and what comes inside a release. One switch at the top for
// automatic updates.

import { get, post, put } from '../api.js';
import { button, clear, confirmDialog, h, icon, pageHeader, showError, switchControl, toast } from '../ui.js';

let poll = null;
const WRAP = { whiteSpace: 'normal', overflow: 'visible' };   // these say more than one line's worth

export async function show(main) {
  clearTimeout(poll);
  let u;
  try { u = await get('/api/updates'); } catch (e) { showError(e); return; }
  const refresh = () => show(main);
  const busy = u.juk3box.phase || u.juk3box.starting || u.os.phase;
  // Quicker while Raspberry Pi OS installs (its queue moves); jb-admin restarts partway through a Juk3box update.
  if (busy) poll = setTimeout(refresh, u.os.phase ? 1500 : 5000);
  clear(main,
    pageHeader('Updates', 'Everything on the jukebox that updates, in one place.'),
    h('div.stack', { style: { maxWidth: '720px' } },
      autoCard(u),
      u.os.restart_needed ? restartNotice() : null,
      juk3boxCard(u.juk3box, refresh),
      osCard(u.os, refresh),
      rootfsCard(u.rootfs, refresh),
      sshCard(u.ssh, refresh),
      keypadCard(u.keypad),
      appCard(),
      insideCard(u)));
}

export function leave() {
  clearTimeout(poll);
}

function when(t) {
  return t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'never';
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

function autoCard(u) {
  return card(
    h('div.phone-row',
      h('div.phone-text',
        h('h2', { style: { marginBottom: '4px' } }, 'Automatic updates'),
        h('div.stat-sub', { style: WRAP }, u.auto
          ? 'On: each night between 3:30 and 4:30, Juk3box updates itself and installs Raspberry Pi OS\'s security fixes. If one needs a restart, the jukebox restarts before 5 when nothing is playing.'
          : 'Off: nothing installs by itself. Check and install here.')),
      switchControl(u.auto, async on => {
        try { await put('/api/update/auto', { on }); toast(on ? 'Automatic updates are on.' : 'Automatic updates are off.', 'success'); }
        catch (e) { showError(e); }
      }, u.auto ? 'On' : 'Off')));
}

function restartNotice() {
  return h('div.notice', icon('power'),
    h('span', { style: { flex: 1 } }, 'Some updates take effect when the jukebox restarts.'),
    button('Restart now', { onclick: async () => {
      const ok = await confirmDialog('Restart the jukebox?', 'The music stops, and the jukebox is back in about a minute.', { confirm: 'Restart' });
      if (!ok) return;
      try { await post('/api/updates/restart', {}); toast('Restarting. This page comes back when the jukebox does.', 'success'); }
      catch (e) { showError(e); }
    } }, 'power'));
}

const PHASES = { 'to install': 'Restarting to install (the system drive is protected)…',
  downloading: 'Downloading the update…', checking: 'Checking it\'s genuine…', installing: 'Installing…',
  restarting: 'Restarting the jukebox…', 'rolling back': 'That didn\'t work; putting the last version back…' };

const STEPS = [['downloading', 'Download'], ['checking', 'Check it\'s genuine'], ['installing', 'Install'],
  ['restarting', 'Restart the jukebox']];

// Updates in order: a spinner on the one under way, "Complete" when it's done.
function queue(items, title) {
  const mark = state => state === 'running' ? h('span.spinner', { 'aria-label': 'Installing' })
    : state === 'done' ? h('span.q-done', icon('circle-check'), 'Complete')
    : state === 'failed' ? h('span.q-failed', icon('circle-x'), 'Failed')
    : h('span.q-waiting', 'Waiting');
  return h('div.update-queue',
    title ? h('div.q-title', title) : null,
    h('ol', items.map(q => h(`li.q-${q.state}`, h('span.q-name', q.name), h('span.q-mark', mark(q.state))))));
}

function juk3boxCard(j, refresh) {
  const busy = j.phase || j.starting;
  let line;
  if (!j.current) line = j.note || 'A development copy: updated by deploying.';
  else if (busy) line = PHASES[j.phase] || 'Starting the update…';
  else if (j.available) line = `${j.latest} is ready (this jukebox has ${j.current}).`;
  else line = `Up to date: ${j.current}. Last checked ${when(j.checked_at)}.`;
  const act = async what => {
    try { await post(`/api/update/${what}`, {}); refresh(); } catch (e) { showError(e); }
  };
  const install = j.available && !busy ? button('Update now', { variant: 'primary', onclick: async () => {
    const ok = await confirmDialog(`Update to ${j.latest}?`,
      'The jukebox stops for a few minutes while it installs, then restarts. If the new version has trouble, the old one comes back by itself.',
      { confirm: 'Update now' });
    if (ok) { toast('Updating. This page catches up when it\'s done.', 'success'); act('apply'); }
  } }, 'download') : null;
  const steps = busy ? queue(STEPS.map(([phase, label]) => {
    const at = STEPS.findIndex(([p]) => p === (j.phase || 'downloading'));
    const i = STEPS.findIndex(([p]) => p === phase);
    return { name: label, state: i < at ? 'done' : i === at ? 'running' : 'waiting' };
  })) : null;
  return card(
    row('package', 'Juk3box', line, install, j.current && !busy ? button('Check now', { onclick: () => act('check') }, 'refresh-cw') : null),
    steps,
    j.error ? h('div.notice', { style: { marginTop: '12px' } }, icon('triangle-alert'), h('span', j.error)) : null);
}

function osCard(o, refresh) {
  const act = async (what, msg) => {
    try { await post(`/api/updates/os/${what}`, {}); if (msg) toast(msg, 'success'); refresh(); } catch (e) { showError(e); }
  };
  const n = o.security.length + o.other.length;
  let line;
  const done = o.queue.filter(q => q.state === 'done').length;
  if (o.phase === 'to install') line = 'Restarting to install (the system drive is protected)…';
  else if (o.phase) line = !done && o.download < 100 && o.queue.length
    ? `Downloading ${o.queue.length} update${o.queue.length === 1 ? '' : 's'}… ${o.download}%`
    : `Installing ${done + 1 > o.queue.length ? o.queue.length : done + 1} of ${o.queue.length}…`;
  else if (!o.checked_at) line = 'Not checked yet.';
  else if (!n) line = `Up to date. Last checked ${when(o.checked_at)}.`;
  else line = [o.security.length ? `${o.security.length} security fix${o.security.length === 1 ? '' : 'es'}` : null,
    o.other.length ? `${o.other.length} other update${o.other.length === 1 ? '' : 's'}` : null].filter(Boolean).join(' and ')
    + ` ready. Last checked ${when(o.checked_at)}.`;
  const security = o.security.length && !o.phase ? button('Install security fixes', { variant: 'primary',
    onclick: () => act('security', 'Installing the security fixes.') }, 'shield-check') : null;
  const all = o.other.length && !o.phase ? button('Install everything', { onclick: async () => {
    const ok = await confirmDialog('Install every Raspberry Pi OS update?',
      'This includes programs the jukebox leans on (Chromium, sound, the kernel). It\'s usually fine, but a new version can change how they behave; Juk3box releases are tested with them. The jukebox needs a restart afterwards.',
      { confirm: 'Install everything' });
    if (ok) act('all', 'Installing every update.');
  } }, 'download') : null;
  const check = o.phase ? null : button('Check now', { onclick: () => act('check', 'Checking. This takes a minute.') }, 'refresh-cw');
  const list = (title, pkgs) => pkgs.length ? h('details.advanced', h('summary', `${title} (${pkgs.length})`),
    h('div.advanced-body', h('dl.setup-summary', pkgs.flatMap(p => [h('dt', p.name), h('dd', `${p.from} → ${p.to}`)])))) : null;
  // The queue while it installs, and the last one for half an hour after (all Complete).
  const recent = o.updated_at && Date.now() / 1000 - o.updated_at < 1800;
  const shown = o.queue.length && (o.phase || recent)
    ? queue(o.queue, o.phase ? null : `Installed ${when(o.updated_at)}`) : null;
  return card(
    row('cpu', 'Raspberry Pi OS', line, security, all, check),
    shown,
    o.error ? h('div.notice', { style: { marginTop: '12px' } }, icon('triangle-alert'), h('span', o.error)) : null,
    o.phase ? null : list('Security fixes', o.security), o.phase ? null : list('Other updates', o.other));
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

function keypadCard(k) {
  let line;
  if (!k.running) line = 'No Juk3box keypad board is plugged in (or it runs other software).';
  else if (k.available) line = `Version ${k.latest} is ready (the board has ${k.running}).`;
  else line = `Up to date: version ${k.running}.`;
  const install = k.available ? h('a.btn.primary', { href: '/setup?step=controls', target: '_blank', rel: 'noopener' },
    icon('download'), h('span', 'Install it')) : null;
  return card(row('keyboard', 'The keypad board', line, install));
}

function appCard() {
  return card(row('smartphone', 'Juk3box Helper (iPhone)', 'The App Store updates it on each phone.'));
}

function insideCard(u) {
  return card(
    h('h2', { style: { marginBottom: '6px' } }, 'What\'s inside'),
    h('p.muted', 'These come inside each Juk3box release, tested together, so they update with it and never one by one.'),
    h('dl.setup-summary', { style: { marginTop: '12px' } },
      u.included.flatMap(p => [h('dt', p.name), h('dd', `${p.version} · ${p.kind}`)])),
    u.system.length ? h('details.advanced', h('summary', 'From Raspberry Pi OS'),
      h('div.advanced-body', h('dl.setup-summary', u.system.flatMap(p => [h('dt', p.name), h('dd', `${p.version} · ${p.does}`)])))) : null,
    h('p.muted', { style: { marginTop: '12px' } }, 'Licenses: THIRD-PARTY.md in the project.'));
}
