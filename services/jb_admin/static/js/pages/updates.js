// Updates: everything on the jukebox that updates, in one place
// (services/jb_admin/updates.py): Juk3box, Raspberry Pi OS, the keypad
// board, and what comes inside a release. One switch at the top for
// automatic updates.

import { get, post, put } from '../api.js';
import { button, clear, confirmDialog, h, icon, pageHeader, showError, switchControl, toast } from '../ui.js';

let poll = null;

export async function show(main) {
  clearTimeout(poll);
  let u;
  try { u = await get('/api/updates'); } catch (e) { showError(e); return; }
  const refresh = () => show(main);
  const busy = u.juk3box.phase || u.juk3box.starting || u.os.phase;
  if (busy) poll = setTimeout(refresh, 5000);      // jb-admin restarts partway through a Juk3box update
  clear(main,
    pageHeader('Updates', 'Everything on the jukebox that updates, in one place.'),
    h('div.stack', { style: { maxWidth: '720px' } },
      autoCard(u),
      u.os.restart_needed ? restartNotice() : null,
      juk3boxCard(u.juk3box, refresh),
      osCard(u.os, refresh),
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
    h('div.phone-text', h('div.stat-value', title), sub ? h('div.stat-sub', sub) : null),
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
        h('div.stat-sub', u.auto
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

const PHASES = { downloading: 'Downloading the update…', checking: 'Checking it\'s genuine…', installing: 'Installing…',
  restarting: 'Restarting the jukebox…', 'rolling back': 'That didn\'t work; putting the last version back…' };

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
  return card(
    row('package', 'Juk3box', line, install, j.current && !busy ? button('Check now', { onclick: () => act('check') }, 'refresh-cw') : null),
    j.error ? h('div.notice', { style: { marginTop: '12px' } }, icon('triangle-alert'), h('span', j.error)) : null);
}

function osCard(o, refresh) {
  const act = async (what, msg) => {
    try { await post(`/api/updates/os/${what}`, {}); if (msg) toast(msg, 'success'); refresh(); } catch (e) { showError(e); }
  };
  const n = o.security.length + o.other.length;
  let line;
  if (o.phase) line = o.what === 'all' ? 'Installing every update…' : 'Installing the security fixes…';
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
  return card(
    row('cpu', 'Raspberry Pi OS', line, security, all, check),
    o.error ? h('div.notice', { style: { marginTop: '12px' } }, icon('triangle-alert'), h('span', o.error)) : null,
    list('Security fixes', o.security), list('Other updates', o.other));
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
