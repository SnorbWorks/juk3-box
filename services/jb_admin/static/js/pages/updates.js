// Updates: everything on the jukebox that updates, in one place
// (services/jb_admin/updates.py): Juk3box, Raspberry Pi OS, the keypad
// board, and what comes inside a release. One switch at the top for
// automatic updates.

import { get, post, put } from '../api.js';
import { button, clear, confirmDialog, h, icon, pageHeader, showError, switchControl, toast } from '../ui.js';

let poll = null;
let showing = 0;               // the newest show(): one overtaken while it waited draws nothing
// An install this page is seeing through, across the restarts it brings (jb-admin restarts
// partway through; on a protected system the whole jukebox restarts, twice). For juk3box and
// os, each while it goes: {to: the version, from: its phase_at before, phase: the step last
// seen, seen: a step was, toInstall: it restarted to install}. protect: it restarts to install
// first; since: when this page began following.
let following = null;
const FOLLOW_MS = 45 * 60 * 1000;     // give up after this (most take 3 to 10 minutes)
const LOST_MS = 150 * 1000;           // asked, and never taken up (the admin says "starting" for 2 minutes)
const WRAP = { whiteSpace: 'normal', overflow: 'visible' };   // these say more than one line's worth

export async function show(main) {
  clearTimeout(poll);
  const mine = ++showing;
  let u;
  try {
    u = await get('/api/updates');
  } catch (e) {
    if (mine !== showing) return;
    // Installing restarts the admin, or the whole jukebox: wait for it to come back.
    if (following && e.status !== 401 && Date.now() - following.since < FOLLOW_MS) {
      away(main);
      poll = setTimeout(() => show(main), 4000);
      return;
    }
    following = null;
    showError(e);
    return;
  }
  if (mine !== showing) return;
  follow(u);
  const j = u.juk3box, o = u.os;
  // Quicker while it starts and while Raspberry Pi OS installs (its queue moves).
  if (following || j.phase || j.starting || o.phase || o.starting) {
    poll = setTimeout(() => show(main), j.starting || o.starting || o.phase ? 1500 : 4000);
  }
  draw(main, u);
}

function draw(main, u) {
  clear(main,
    pageHeader('Updates', 'Everything on the jukebox that updates, in one place.'),
    h('div.stack', { style: { maxWidth: '720px' } },
      autoCard(u),
      u.os.restart_needed ? restartNotice() : null,
      juk3boxCard(main, u),
      osCard(main, u),
      keypadCard(u.keypad),
      appCard(),
      insideCard(u)));
}

// Each install's progress, from each answer. One's over when it says so (nothing under way,
// and its state written since it began) or when it's on the version it went for. Between
// steps (a restart, the next step starting) nothing says busy for a moment: that alone isn't over.
function follow(u) {
  const busy = s => Boolean(s.phase || s.starting);
  if (!following && !busy(u.juk3box) && !busy(u.os)) return;
  const f = following ??= { juk3box: null, os: null, protect: Boolean(u.rootfs?.protected), since: Date.now() };
  for (const part of ['juk3box', 'os']) {
    const s = u[part];
    if (busy(s)) {
      const t = f[part] ??= { to: s.latest, from: s.phase_at || 0 };
      t.phase = s.phase || 'starting';
      t.seen ||= Boolean(s.phase);
      t.toInstall ||= s.phase === 'to install';
      f.protect ||= ['to install', 'protecting'].includes(s.phase);
      continue;
    }
    const t = f[part];
    if (!t) continue;
    const updated = Boolean(t.to) && s.current === t.to;
    if (updated || (s.phase_at || 0) > t.from) {
      f[part] = null;
      if (updated && !s.error) toast(`Updated to ${s.current}.`, 'success');
    } else if (!t.seen && Date.now() - f.since > LOST_MS) {
      f[part] = null;
      toast('The update didn\'t start. Try again in a minute.', 'error');
    }
  }
  if ((!f.juk3box && !f.os) || Date.now() - f.since > FOLLOW_MS) following = null;
}

// Following one more install (the buttons), from this answer.
function begin(u, part) {
  const s = u[part];
  following ??= { juk3box: null, os: null, protect: Boolean(u.rootfs?.protected), since: Date.now() };
  following[part] = { to: s.latest, from: s.phase_at || 0, phase: 'starting', seen: false };
}

// While the admin, or the whole jukebox, restarts partway through.
function away(main) {
  const t = following.juk3box || following.os;
  const [why, label] = t?.phase === 'to install' ? ['The jukebox is restarting to install.', DOING['to install']]
    : t?.phase === 'protecting' ? ['Installed. The jukebox is restarting once more to protect the system drive.', DOING.protecting]
    : ['The jukebox is restarting.', 'Restarting…'];
  clear(main,
    pageHeader('Updates', 'Everything on the jukebox that updates, in one place.'),
    h('div.stack', { style: { maxWidth: '720px' } },
      card(row('package', following.juk3box?.to ? `Juk3box ${following.juk3box.to}` : 'Updates',
        `${why} This page picks up again by itself when it's back.`, working(label)))));
}

// An install button while it works: what it's doing, and not to be pressed again.
function working(label) {
  return h('button.btn.primary.working', { type: 'button', disabled: true, 'aria-busy': 'true' },
    h('span.spinner'), h('span.btn-label', label));
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

const PHASES = { starting: 'Starting the update…', 'to install': 'Restarting to install (the system drive is protected)…',
  downloading: 'Downloading the update…', checking: 'Checking it\'s genuine…', installing: 'Installing…',
  restarting: 'Restarting the jukebox…', protecting: 'Installed. Restarting once more to protect the system drive…',
  'rolling back': 'That didn\'t work; putting the last version back…' };
// The Update now button's words while it works.
const DOING = { starting: 'Starting…', 'to install': 'Restarting to install…', downloading: 'Downloading update…',
  checking: 'Checking update…', installing: 'Installing now…', restarting: 'Restarting…', protecting: 'Finishing up…',
  'rolling back': 'Undoing…' };

const STEPS = [['downloading', 'Download'], ['checking', 'Check it\'s genuine'], ['installing', 'Install'],
  ['restarting', 'Restart the jukebox']];
// On a protected system: a restart to install first, and one to protect it again last.
const PROTECTED_STEPS = [['to install', 'Restart to install'], ...STEPS, ['protecting', 'Protect the system drive again']];

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

function juk3boxCard(main, u) {
  const j = u.juk3box, t = following?.juk3box;
  // Between steps (a restart) nothing's under way for a moment: the step last seen.
  const phase = j.phase || (j.starting ? 'starting' : t ? t.phase : null);
  const busy = Boolean(phase);
  let line;
  if (!j.current) line = j.note || 'A development copy: updated by deploying.';
  else if (busy) line = PHASES[phase] || 'Starting the update…';
  else if (j.available) line = `${j.latest} is ready (this jukebox has ${j.current}).`;
  else line = `Up to date: ${j.current}. Last checked ${when(j.checked_at)}.`;
  const check = async () => {
    try { await post('/api/update/check', {}); show(main); } catch (e) { showError(e); }
  };
  let install = null;
  if (busy) install = working(DOING[phase] || 'Updating…');
  else if (j.available) install = button('Update now', { variant: 'primary', onclick: async () => {
    const ok = await confirmDialog(`Update to ${j.latest}?`,
      'The jukebox stops for a few minutes while it installs, then restarts. If the new version has trouble, the old one comes back by itself.',
      { confirm: 'Update now' });
    if (!ok) return;
    begin(u, 'juk3box');
    draw(main, u);                      // the button says so at once
    try { await post('/api/update/apply', {}); } catch (e) { if (following) following.juk3box = null; showError(e); }
    show(main);
  } }, 'download');
  // "starting" again after the restart to install: the download's next.
  const list = following?.protect ? PROTECTED_STEPS : STEPS;
  const step = phase === 'starting' ? (t?.toInstall ? 'downloading' : list[0][0])
    : phase === 'rolling back' ? 'restarting' : phase;
  const at = list.findIndex(([p]) => p === step);
  const steps = busy ? queue(list.map(([, label], i) => ({ name: label,
    state: i < at ? 'done' : i > at ? 'waiting' : phase === 'rolling back' ? 'failed' : 'running' }))) : null;
  return card(
    row('package', 'Juk3box', line, install, j.current && !busy ? button('Check now', { onclick: check }, 'refresh-cw') : null),
    steps,
    j.error && !busy ? h('div.notice', { style: { marginTop: '12px' } }, icon('triangle-alert'), h('span', j.error)) : null);
}

function osCard(main, u) {
  const o = u.os, t = following?.os;
  const install = async what => {
    begin(u, 'os');
    draw(main, u);                      // the button says so at once
    try { await post(`/api/updates/os/${what}`, {}); } catch (e) { if (following) following.os = null; showError(e); }
    show(main);
  };
  const phase = o.phase || (o.starting ? 'starting' : t ? t.phase : null);
  const busy = Boolean(phase);
  const n = o.security.length + o.other.length;
  let line;
  const done = o.queue.filter(q => q.state === 'done').length;
  const downloading = !done && o.download < 100 && o.queue.length > 0;
  if (phase === 'starting') line = 'Starting the install…';
  else if (phase === 'to install') line = 'Restarting to install (the system drive is protected)…';
  else if (phase === 'protecting') line = 'Installed. Restarting once more to protect the system drive…';
  else if (phase) line = downloading
    ? `Downloading ${o.queue.length} update${o.queue.length === 1 ? '' : 's'}… ${o.download}%`
    : `Installing ${done + 1 > o.queue.length ? o.queue.length : done + 1} of ${o.queue.length}…`;
  else if (!o.checked_at) line = 'Not checked yet.';
  else if (!n) line = `Up to date. Last checked ${when(o.checked_at)}.`;
  else line = [o.security.length ? `${o.security.length} security fix${o.security.length === 1 ? '' : 'es'}` : null,
    o.other.length ? `${o.other.length} other update${o.other.length === 1 ? '' : 's'}` : null].filter(Boolean).join(' and ')
    + ` ready. Last checked ${when(o.checked_at)}.`;
  const doing = busy ? working(DOING[phase] && phase !== 'installing' ? DOING[phase]
    : downloading ? 'Downloading…' : 'Installing now…') : null;
  const security = o.security.length && !busy ? button('Install security fixes', { variant: 'primary',
    onclick: () => install('security') }, 'shield-check') : null;
  const all = o.other.length && !busy ? button('Install everything', { onclick: async () => {
    const ok = await confirmDialog('Install every Raspberry Pi OS update?',
      'This includes programs the jukebox leans on (Chromium, sound, the kernel). It\'s usually fine, but a new version can change how they behave; Juk3box releases are tested with them. The jukebox needs a restart afterwards.',
      { confirm: 'Install everything' });
    if (ok) install('all');
  } }, 'download') : null;
  const check = busy ? null : button('Check now', { onclick: async () => {
    try { await post('/api/updates/os/check', {}); toast('Checking. This takes a minute.', 'success'); } catch (e) { showError(e); }
    show(main);
  } }, 'refresh-cw');
  const list = (title, pkgs) => pkgs.length ? h('details.advanced', h('summary', `${title} (${pkgs.length})`),
    h('div.advanced-body', h('dl.setup-summary', pkgs.flatMap(p => [h('dt', p.name), h('dd', `${p.from} → ${p.to}`)])))) : null;
  // The queue while it installs, and the last one for half an hour after (all Complete).
  const recent = o.updated_at && Date.now() / 1000 - o.updated_at < 1800;
  const installed = o.queue.length && (o.phase || (!busy && recent))
    ? queue(o.queue, o.phase ? null : `Installed ${when(o.updated_at)}`) : null;
  return card(
    row('cpu', 'Raspberry Pi OS', line, doing, security, all, check),
    installed,
    o.error && !busy ? h('div.notice', { style: { marginTop: '12px' } }, icon('triangle-alert'), h('span', o.error)) : null,
    busy ? null : list('Security fixes', o.security), busy ? null : list('Other updates', o.other));
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
