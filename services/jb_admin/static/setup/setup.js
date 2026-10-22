// The setup wizard's page (docs/Setup Wizard — Plan.md). The Pi keeps the
// progress (/api/wizard); this page shows one step at a time and sends the
// answers. Steps that watch the cabinet (the light check, the power check,
// the sound, the controls) poll while they're open.

const root = document.getElementById('wizard');
// Inside the Juk3box Helper app, which has its own title bar.
if (/Juk3boxHelper/.test(navigator.userAgent)) document.documentElement.classList.add('in-app');
const POLL_MS = 1500;
const LIVE_STEPS = new Set(['wifi', 'lightcheck', 'loadtest', 'sound', 'controls', 'sources']);
const TEACH_POLL_MS = 400;   // the controls step follows each press at the cabinet
// The Juk3box Helper app opens setup with the phone's own network to suggest.
const SUGGESTED_SSID = new URLSearchParams(location.search).get('ssid');
// Admin › Setup opens one step on its own (/setup?step=controls): just that
// step, then back to the admin.
const ONLY_STEP = new URLSearchParams(location.search).get('step');
const COLORS = ['#22e7d4', '#009da4', '#0068a6', '#ff2198', '#ff63d5', '#ff9959'];

let view = null;          // the server's view of the open step
let local = {};           // this page's own state for the open step
let pollTimer = null;
let busy = false;

// -- Plumbing -----------------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: { 'X-JB-Admin': '1' }, credentials: 'same-origin' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    return { ok: false, status: 0, data: { error: "Can't reach the jukebox." } };
  }
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  return { ok: res.ok, status: res.status, data };
}

function h(tag, attrs = {}, ...children) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (classes.length) el.className = classes.join(' ');
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') el.className += ` ${v}`;
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const SVG = 'http://www.w3.org/2000/svg';
function icon(d, { size = 22, color = 'currentColor', width = 2.2 } = {}) {
  const svg = document.createElementNS(SVG, 'svg');
  for (const [k, v] of Object.entries({ width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: color,
    'stroke-width': width, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(k, v);
  for (const part of [].concat(d)) {
    const p = document.createElementNS(SVG, 'path');
    p.setAttribute('d', part);
    svg.append(p);
  }
  return svg;
}
const I = {
  check: 'M5 12.5l4.5 4.5L19 7.5',
  info: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18', 'M12 11v6', 'M12 7.5v.5'],
  warn: ['M12 3l10 18H2z', 'M12 10v5', 'M12 18v.5'],
  pins: ['M4 6h16', 'M4 12h16', 'M4 18h16', 'M8 3v18', 'M16 3v18'],
  plug: ['M9 2v6', 'M15 2v6', 'M6 8h12v4a6 6 0 0 1-12 0z', 'M12 18v4'],
  wifi: ['M2 8.5a15 15 0 0 1 20 0', 'M5 12a10.5 10.5 0 0 1 14 0', 'M8.5 15.5a5.5 5.5 0 0 1 7 0', 'M12 19v.5'],
  cable: ['M7 3v5', 'M17 3v5', 'M5 8h14v3a7 7 0 0 1-14 0z', 'M12 18v3'],
  chip: ['M7 7h10v10H7z', 'M9 3v4', 'M15 3v4', 'M9 17v4', 'M15 17v4', 'M3 9h4', 'M3 15h4', 'M17 9h4', 'M17 15h4'],
  cabinet: ['M6 2.5h12a1 1 0 0 1 1 1v17a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-17a1 1 0 0 1 1-1z', 'M8 9h8', 'M8 13h8', 'M10 17h4'],
  fan: ['M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0', 'M12 10c0-4 1-7 4-7s3 4-2 7', 'M14 12c4 0 7 1 7 4s-4 3-7-2', 'M12 14c0 4-1 7-4 7s-3-4 2-7', 'M10 12c-4 0-7-1-7-4s4-3 7 2'],
  phone: ['M8 2.5h8a1 1 0 0 1 1 1v17a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1v-17a1 1 0 0 1 1-1z', 'M11 18.5h2'],
  bt: 'M7 7l10 10-5 4V3l5 4L7 17',
  usb: ['M12 3v14', 'M9 6l3-3 3 3', 'M12 17a2 2 0 1 0 0 4a2 2 0 1 0 0-4', 'M12 13l-4-2V8', 'M12 11l4-2V7'],
  speaker: ['M6 3h12v18H6z', 'M12 14m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0', 'M12 7v.5'],
  clock: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18', 'M12 7v5l3 2'],
};

function eq(height = 40) {
  return h('div.eq', { 'aria-hidden': 'true', style: `height:${height}px` },
    COLORS.map((c, i) => h('i', { style: `background:${c};animation-duration:${1.1 + (i * 0.17) % 0.6}s` })));
}

function note(kind, text, extra) {
  const marks = { ok: [I.check, '#15803d'], warn: [I.warn, '#c2410c'], bad: [I.warn, '#b91c1c'], info: [I.info, '#7a3db0'] };
  const [d, color] = marks[kind] || marks.info;
  return h(`div.wz-note.${kind}`, {}, icon(d, { color, size: 22 }), h('div', {}, text, extra || null));
}

function choice({ title, sub, on, onClick, kind = 'box', iconPath }) {
  const lead = iconPath
    ? h('span.wz-icon', {}, icon(iconPath, { color: '#5b2491' }))
    : h(`span.wz-box${kind === 'radio' ? '.wz-radio' : ''}`, {}, icon(I.check, { color: '#fff', size: 16, width: 3.5 }));
  return h(`button.wz-choice${on ? '.on' : ''}`, { type: 'button', 'aria-pressed': on ? 'true' : 'false', onClick },
    lead, h('span', {}, h('span.t', {}, title), sub ? h('span.s', {}, sub) : null));
}

function stat(k, v) {
  return h('div.wz-stat', {}, h('div.k', {}, k), h('div.v', {}, v));
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// -- Loading and moving ----------------------------------------------------------

async function load(step) {
  const r = await api('GET', `/api/wizard${step ? `?step=${encodeURIComponent(step)}` : ''}`);
  if (r.status === 401) return renderLogin();
  if (!r.ok) return renderTrouble(r.data.error);
  show(r.data);
}

function show(next) {
  const moved = !view || next.step.id !== view.step.id;
  view = next;
  if (moved) { local = {}; window.scrollTo(0, 0); }
  render();
  if (moved) enter();
}

// A step that does something as soon as it opens.
function enter() {
  const id = view.step.id;
  if (id === 'lightcheck' && view.step.chains.length) showChain(0);
}

async function submit(data = {}) {
  if (busy) return;
  busy = true;
  local.error = null;
  render();
  const r = await api('POST', `/api/wizard/${view.step.id}`, data);
  busy = false;
  if (r.ok && r.data.restarting) return renderRestarting();
  if (r.ok && r.data.done) return renderFinished(true);
  if (r.ok && ONLY_STEP) return renderSaved();
  if (!r.ok) {
    local.error = r.data.error || 'That didn\'t work. Try again.';
    return render();
  }
  window.scrollTo(0, 0);
  show(r.data);
}

async function act(name, data = {}, { quiet = false } = {}) {
  if (!quiet) { local.acting = name; local.error = null; render(); }
  const r = await api('POST', `/api/wizard/${view.step.id}/${name}`, data);
  local.acting = null;
  if (!r.ok) local.error = r.data.error || 'That didn\'t work. Try again.';
  render();
  return r.ok ? r.data : null;
}

function startPolling() {
  stopPolling();
  if (!view || !LIVE_STEPS.has(view.step.id)) return;
  if (view.step.id === 'controls' && view.step.phase === 'format') return;
  pollTimer = setInterval(async () => {
    if (busy || document.hidden) return;
    const r = await api('GET', `/api/wizard?step=${view.step.id}`);
    if (r.ok && r.data.step.id === view.step.id) {
      view = r.data;
      onLive();
      // Not while someone's typing (a Wi-Fi password): redrawing would close
      // the phone's keyboard. The news shows at the next redraw.
      if (!typing()) render();
    }
  }, view.step.id === 'controls' ? TEACH_POLL_MS : POLL_MS);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

// -- The frame ----------------------------------------------------------------------

function typing() {
  const a = document.activeElement;
  return !!a && root.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName);
}

function render() {
  stopPolling();
  if (!view) return;
  // Redrawing replaces the page: keep where it was scrolled to, and the box
  // being typed in (with its cursor), so a redraw never jumps or drops it.
  const y = window.scrollY;
  const a = document.activeElement;
  const focusId = a && root.contains(a) && a.id ? a.id : null;
  const caret = focusId && typeof a.selectionStart === 'number' ? [a.selectionStart, a.selectionEnd] : null;
  draw();
  if (focusId) {
    const again = document.getElementById(focusId);
    if (again) {
      again.focus({ preventScroll: true });
      if (caret) try { again.setSelectionRange(...caret); } catch { /* not a text box */ }
    }
  }
  window.scrollTo(0, y);
}

function draw() {
  if (view.done && !local.again && !ONLY_STEP) return renderFinished(false);
  if (!ONLY_STEP && !seenWelcome() && !view.steps.some(s => s.done)) return renderWelcome();
  const r = STEPS[view.step.id] ? STEPS[view.step.id](view.step) : { body: [h('p', {}, 'This step is coming soon.')] };
  frame(r);
  startPolling();
}

function seenWelcome() {
  try { return sessionStorage.getItem('jbSetupWelcome') === '1'; } catch { return local.welcomed; }
}

function frame({ body, next, back = true, skip = false }) {
  const steps = view.steps;
  const i = steps.findIndex(s => s.id === view.step.id);
  const n = i + 1;
  const stepLabel = view.step.id === 'password' ? 'Getting started' : `Step ${n} of ${steps.length}`;
  const side = h('aside.wz-side', {},
    h('img', { src: '/static/setup/logo.png', alt: 'Juk3box' }),
    h('ol', {}, steps.map((s, k) => h(`li${s.id === view.step.id ? '.now' : s.done ? '.done' : ''}`,
      s.id === view.step.id ? { 'aria-current': 'step' } : {},
      h('span.n', {}, s.done && s.id !== view.step.id ? icon(I.check, { color: '#fff', size: 14, width: 3.5 }) : String(k + 1)),
      s.short))),
    h('p.wz-note', {}, 'Stop anytime. Your answers are saved on the jukebox, so you can pick up from any phone or computer.'));
  const top = h('header.wz-top', {},
    h('img', { src: '/static/setup/logo.png', alt: 'Juk3box' }),
    h('div.wz-progress', {}, h('div', {}, stepLabel),
      h('div.wz-dots', { 'aria-hidden': 'true' }, steps.map((s, k) => h(`span${k === i ? '.now' : s.done ? '.done' : ''}`)))));
  const foot = h('div.wz-foot', {},
    back && view.step.back && !ONLY_STEP ? h('button.wz-back', { type: 'button', onClick: () => load(view.step.back) }, 'Back') : null,
    h('span.wz-spacer'),
    skip && view.step.skippable ? h('button.wz-skip', { type: 'button', onClick: () => submit({ skip: true }) }, 'Skip for now') : null,
    next ? h('button.wz-next', { type: 'button', disabled: next.disabled || busy, onClick: next.onClick },
      busy ? h('span.wz-spin') : null, next.label || 'Next') : null);
  const card = h('main.wz-card', {},
    h('div.wz-body', {},
      h('div.wz-kicker', { class: 'desk' }, view.step.id === 'password' ? '' : `Step ${n} of ${steps.length}`),
      h('h2', {}, view.step.title),
      body,
      local.error ? h('div.wz-error', { role: 'alert' }, local.error) : null),
    foot);
  root.replaceChildren(h('div.wz-steps', {}, top, side, card));
}

function renderWelcome() {
  root.replaceChildren(h('section.wz-hero', {},
    h('img.wz-hero-logo', { src: '/static/setup/logo.png', alt: 'Juk3box' }),
    eq(),
    h('h1', {}, 'Welcome to your jukebox'),
    h('p', {}, 'Let\'s get it set up. It takes about ten minutes, and you can stop and come back anytime.'),
    h('button.wz-next', { type: 'button', onClick: () => {
      try { sessionStorage.setItem('jbSetupWelcome', '1'); } catch { local.welcomed = true; }
      render();
      enter();
    } }, 'Let\'s go')));
}

function renderFinished(justNow) {
  stopPolling();
  const name = (view?.step?.summary || []).find(r => r.k === 'Name')?.v || 'Your jukebox';
  root.replaceChildren(h('section.wz-hero', {},
    h('img.wz-hero-logo', { src: '/static/setup/logo.png', alt: 'Juk3box' }),
    eq(44),
    h('h1', {}, justNow ? `${name} is ready` : 'Setup is finished'),
    h('p', {}, justNow ? 'Pick a song on the cabinet and enjoy. You can change anything later in the admin.'
      : 'This jukebox is set up. Change anything in the admin, or run setup again.'),
    h('a.wz-next', { href: '/', style: 'text-decoration:none' }, 'Open the admin'),
    justNow ? null : h('button.wz-ghost', { type: 'button', style: 'background:transparent;color:#fff;border-color:rgba(255,255,255,.5);width:100%;max-width:360px', onClick: async () => {
      const r = await api('POST', '/api/wizard-again', {});
      if (r.ok) { local = { again: true }; load(); }
    } }, 'Run setup again')));
}

// One step opened from the admin, saved.
function renderSaved() {
  stopPolling();
  root.replaceChildren(h('section.wz-hero', {},
    h('img.wz-hero-logo', { src: '/static/setup/logo.png', alt: 'Juk3box' }),
    eq(44),
    h('h1', {}, 'Saved'),
    h('p', {}, 'The jukebox is using it now.'),
    h('a.wz-next', { href: '/#/setup', style: 'text-decoration:none' }, 'Back to the admin')));
}

function renderRestarting() {
  stopPolling();
  root.replaceChildren(h('section.wz-hero', {},
    h('img.wz-hero-logo', { src: '/static/setup/logo.png', alt: 'Juk3box' }),
    h('div.wz-spin', { style: 'width:40px;height:40px;border-width:4px' }),
    h('h1', {}, 'Restarting'),
    h('p', {}, 'Your jukebox is restarting to apply your settings. It takes about a minute, and this page carries on by itself.')));
  const started = Date.now();
  const tick = async () => {
    if (Date.now() - started > 15000) {
      const r = await api('GET', '/api/wizard');
      if (r.ok && r.data.step.id !== 'apply') { view = null; return show(r.data); }
    }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 3000);
}

function renderLogin() {
  let pw = '';
  const form = h('form.wz-body', { style: 'margin:0 auto', onSubmit: async e => {
    e.preventDefault();
    const r = await api('POST', '/api/login', { password: pw });
    if (r.ok) return load();
    err.textContent = r.data.error || 'That password isn\'t right.';
    err.hidden = false;
  } },
    h('h2', {}, 'Sign in to keep going'),
    h('p', {}, 'This jukebox already has a password. Sign in with it to carry on with setup.'),
    h('div.wz-field', {}, h('label.wz-label', { for: 'pw' }, 'Password'),
      h('input.wz-input', { id: 'pw', type: 'password', autocomplete: 'current-password', onInput: e => { pw = e.target.value; } })),
    h('button.wz-next', { type: 'submit', style: 'flex:none' }, 'Sign in'));
  const err = h('div.wz-error', { role: 'alert', hidden: true });
  form.append(err);
  root.replaceChildren(h('div.wz-steps', {},
    h('header.wz-top', {}, h('img', { src: '/static/setup/logo.png', alt: 'Juk3box' })),
    h('aside.wz-side', {}, h('img', { src: '/static/setup/logo.png', alt: 'Juk3box' })),
    h('main.wz-card', {}, form)));
}

function renderTrouble(message) {
  root.replaceChildren(h('section.wz-hero', {},
    h('img.wz-hero-logo', { src: '/static/setup/logo.png', alt: 'Juk3box' }),
    h('h1', {}, 'One moment'),
    h('p', {}, message || 'The jukebox didn\'t answer. Check that this phone or computer is on the same network, then try again.'),
    h('button.wz-next', { type: 'button', onClick: () => load() }, 'Try again')));
}

// Live answers from the cabinet's knob (light check, sound), and how a
// Wi-Fi join went.
function onLive() {
  const s = view.step;
  if (s.id === 'wifi' && local.joining && s.join?.id === local.joinId && s.join.state === 'failed') {
    local.joining = null;
    local.failedId = local.joinId;
  }
  if (s.id === 'lightcheck' && local.phase === 'asking') {
    const a = s.cabinet_answer;
    if (a && a.chain === s.chains[local.idx]?.id && a.at * 1000 > (local.shownAt || 0)) answerChain(a.yes);
  }
  if (s.id === 'sound' && local.played && local.heard === undefined) {
    const a = s.cabinet_answer;
    if (a && a.at * 1000 > (local.playedAt || 0)) local.heard = a.yes;
  }
}

// -- The steps -------------------------------------------------------------------------

const STEPS = {
  password(s) {
    local.pw ??= '';
    local.pw2 ??= '';
    const kind = local.showPw ? 'text' : 'password';
    const go = async () => {
      if (local.pw.length < 8) { local.error = 'Use at least 8 characters.'; return render(); }
      if (local.pw !== local.pw2) { local.error = 'The two passwords don\'t match.'; return render(); }
      busy = true; render();
      const r = await api('POST', '/api/setup', { password: local.pw });
      busy = false;
      if (!r.ok) { local.error = r.data.error; return render(); }
      load();
    };
    return {
      body: [
        h('p', {}, 'It keeps your jukebox\'s settings yours. You\'ll use it to open the admin from a phone or computer.'),
        // The account name a phone's password manager files the password
        // under (with this page's address); not shown, there's only one.
        h('input', { id: 'pwuser', type: 'text', name: 'username', autocomplete: 'username', value: 'admin', readonly: true,
          tabindex: '-1', 'aria-hidden': 'true', style: 'position:absolute;left:-9999px;width:1px;height:1px;opacity:0' }),
        h('div.wz-field', {}, h('label.wz-label', { for: 'pw1' }, 'Password'),
          h('input.wz-input', { id: 'pw1', type: kind, name: 'password', autocomplete: 'new-password', value: local.pw, onInput: e => { local.pw = e.target.value; } })),
        h('div.wz-field', {}, h('label.wz-label', { for: 'pw2' }, 'Type it again'),
          h('input.wz-input', { id: 'pw2', type: kind, autocomplete: 'new-password', value: local.pw2, onInput: e => { local.pw2 = e.target.value; } })),
        h('label.wz-switch', {}, h('input', { type: 'checkbox', checked: !!local.showPw, onChange: e => { local.showPw = e.target.checked; render(); } }),
          h('span', {}, 'Show the password')),
        h('p', {}, 'At least 8 characters.'),
        note('info', `If your phone suggests a strong password and saves it, look for it in your Passwords app under ${location.hostname}, user admin.`),
      ],
      next: { label: 'Next', onClick: go },
      back: false,
    };
  },

  name(s) {
    local.name ??= s.name;
    return {
      body: [
        h('p', {}, 'This is how it shows up on your phone, in the AirPlay speaker list and over Bluetooth.'),
        h('div.wz-field', {}, h('label.wz-label', { for: 'jbname' }, 'Name'),
          h('input.wz-input', { id: 'jbname', type: 'text', maxlength: '40', value: local.name, onInput: e => { local.name = e.target.value; } })),
        h('p', {}, 'Ideas: Den Jukebox, The Rec Room, Garage Jukebox.'),
      ],
      next: { onClick: () => submit({ name: local.name }), disabled: !local.name?.trim() },
    };
  },

  wifi(s) {
    if (s.hotspot) return hotspotWifi(s);
    const via = s.via === 'ethernet' ? 'a network cable' : 'Wi-Fi';
    return {
      body: s.via ? [
        h('p', {}, 'The jukebox uses your network for cover art, the weather and your phones.'),
        note('ok', `Connected to ${s.name || 'your network'} over ${via}.`),
      ] : [
        h('p', {}, 'The jukebox isn\'t on a network yet.'),
        note('warn', 'Plug in a network cable, or join the jukebox\'s own setup Wi-Fi from your phone to pick your network.'),
      ],
      next: { onClick: () => submit(), disabled: !s.via },
    };
  },

  time(s) {
    const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const region = (navigator.language || '').split('-')[1];
    local.tz ??= browserTz || s.timezone;
    local.cc ??= s.country || (region && region.length === 2 ? region.toUpperCase() : 'US');
    const zones = (Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : [s.timezone, browserTz]).filter(Boolean);
    if (!zones.includes(local.tz)) zones.unshift(local.tz);
    let names;
    try { names = new Intl.DisplayNames(['en'], { type: 'region' }); } catch { names = null; }
    const countries = [];
    for (let a = 65; a <= 90; a++) for (let b = 65; b <= 90; b++) {
      const cc = String.fromCharCode(a, b);
      const name = names ? names.of(cc) : cc;
      if (name && name !== cc) countries.push([cc, name]);
    }
    countries.sort((x, y) => x[1].localeCompare(y[1]));
    if (!countries.some(c => c[0] === local.cc)) countries.unshift([local.cc, local.cc]);
    return {
      body: [
        h('p', {}, 'We filled these in from this phone or computer. Change them if they\'re wrong.'),
        h('div.wz-field', {}, h('label.wz-label', { for: 'tz' }, 'Time zone'),
          h('select.wz-select', { id: 'tz', onChange: e => { local.tz = e.target.value; } },
            zones.map(z => h('option', { value: z, selected: z === local.tz }, z.replaceAll('_', ' '))))),
        h('div.wz-field', {}, h('label.wz-label', { for: 'cc' }, 'Country'),
          h('select.wz-select', { id: 'cc', onChange: e => { local.cc = e.target.value; } },
            countries.map(([cc, name]) => h('option', { value: cc, selected: cc === local.cc }, name)))),
        h('p', {}, 'The country sets which Wi-Fi channels the jukebox may use.'),
        s.synced ? note('ok', 'The clock is set from the internet.') : null,
      ],
      next: { onClick: () => submit({ timezone: local.tz, country: local.cc }) },
    };
  },

  board(s) {
    const name = s.model ? s.model.replace(/ Model B| Rev [\d.]+/g, '') : 'a Raspberry Pi';
    return {
      body: [
        h('div.wz-center', { style: 'display:flex;flex-direction:column;gap:14px;padding-top:6px' },
          h('span.wz-big-icon', {}, icon(I.chip, { size: 52, color: '#7a3db0', width: 1.6 })),
          h('p', { style: 'font-size:22px;font-weight:800;color:#2a1640' }, `This is ${name}${s.memory_gb ? `, ${s.memory_gb} GB` : ''}.`)),
        s.supported ? h('p', {}, 'The jukebox sets itself up for this board: its lights, its power and its video.')
          : note('warn', 'Juk3box is made for the Raspberry Pi 4 and 5. Setup carries on, but the board-specific steps are skipped.'),
      ],
      next: { label: 'That\'s right', onClick: () => submit() },
    };
  },

  power(s) {
    if (!s.pi5) {
      const low = s.throttled && (s.throttled & 0x50005);
      return {
        body: [
          h('p', {}, 'A Raspberry Pi 4 tells us when its power runs low, so there\'s nothing to set here.'),
          low ? note('warn', 'The Pi has seen low voltage since it started. The power check later will show whether the supply keeps up.')
            : note('ok', 'Power looks good so far. The power check later makes sure.'),
        ],
        next: { onClick: () => submit() },
      };
    }
    if (s.pd) {
      return {
        body: [
          h('p', {}, 'Your jukebox runs on a Raspberry Pi 5.'),
          note('ok', 'A USB-C charger is powering it, and it told the Pi how much it can give. Nothing to set.'),
        ],
        next: { onClick: () => submit() },
      };
    }
    local.source ??= s.source;
    return {
      body: [
        h('p', {}, 'Your jukebox runs on a Raspberry Pi 5. It can\'t tell on its own which of these feeds it.'),
        h('div.wz-choices.two', {},
          choice({ title: 'The cabinet\'s power supply', sub: 'Two wires on the Pi\'s pins, from the same supply as the lights', iconPath: I.pins,
            on: local.source === 'gpio', onClick: () => { local.source = 'gpio'; render(); } }),
          choice({ title: 'A USB-C charger', sub: 'A charger plugged into the round USB-C port on the Pi', iconPath: I.plug,
            on: local.source === 'usbc', onClick: () => { local.source = 'usbc'; render(); } })),
        h('p', {}, 'Not sure? Look where the power wire meets the Pi: a USB-C plug on its edge, or two wires on the pins.'),
      ],
      next: { onClick: () => submit({ source: local.source }), disabled: !local.source },
    };
  },

  battery(s) {
    local.official ??= s.official ?? (s.charging ? true : undefined);
    return {
      body: [
        h('p', {}, 'A small battery keeps the Pi\'s clock running while it\'s unplugged, so it starts up at the right time.'),
        h('div.wz-choices.two', {},
          choice({ title: 'Yes, the official battery', sub: 'The Raspberry Pi RTC Battery, plugged into the BAT connector', kind: 'radio',
            on: local.official === true, onClick: () => { local.official = true; render(); } }),
          choice({ title: 'No battery', sub: 'Or a different one', kind: 'radio',
            on: local.official === false, onClick: () => { local.official = false; render(); } })),
        note('info', 'Only the official battery can be charged. Any other kind is left alone, because charging a battery that isn\'t made for it is dangerous.'),
      ],
      next: { onClick: () => submit({ official: local.official }), disabled: local.official === undefined },
    };
  },

  cooling(s) {
    const spin = local.spin || s.spin;
    return {
      body: s.fan ? [
        h('p', {}, 'A Pi 5 inside a cabinet needs its fan. It runs by itself: off when cool, faster as the Pi warms up.'),
        note('ok', 'Fan found.'),
        h('div.wz-stats', {}, stat('Temperature', s.temp ? `${Math.round(s.temp)} °C` : '–'),
          stat('Fan', spin ? `${(spin['4'] || 0).toLocaleString()} rpm` : s.rpm ? `${s.rpm.toLocaleString()} rpm` : 'Resting')),
        spin ? note(spin['4'] > 0 ? 'ok' : 'bad', spin['4'] > 0 ? 'It spins up and back down. All good.' : 'It didn\'t spin. Check its cable on the FAN connector.')
          : h('button.wz-ghost', { type: 'button', disabled: local.acting === 'spin', onClick: async () => {
            const out = await act('spin');
            if (out) { local.spin = out.spin; render(); }
          } }, local.acting === 'spin' ? [h('span.wz-spin'), 'Spinning it up…'] : 'Spin it to check'),
      ] : [
        note('warn', 'No fan found. A Pi 5 in a closed cabinet needs a cooler, like the official Active Cooler on the FAN connector. The power check keeps an eye on the temperature.'),
      ],
      next: { onClick: () => submit() },
    };
  },

  lights(s) {
    local.chains ??= { ...s.chains };
    const none = !Object.values(local.chains).some(Boolean);
    const subs = { buttons: 'The glowing keypad and control buttons', accent: 'The long strips around the top and the base',
      levelbar: 'The light bar that shows the volume and the beat' };
    return {
      body: [
        h('p', {}, 'Pick all that are wired in. No lights is fine too: everything else works the same.'),
        h('div.wz-choices.two', {},
          Object.keys(s.names).map(id => choice({ title: s.names[id], sub: subs[id], on: local.chains[id],
            onClick: () => { local.chains = { ...local.chains, [id]: !local.chains[id] }; render(); } })),
          choice({ title: 'No lights', sub: 'Skip the light steps', on: none,
            onClick: () => { local.chains = { buttons: false, accent: false, levelbar: false }; render(); } })),
        none ? null : note('info', 'Later, each one lights up in turn so you can check it at the cabinet.'),
      ],
      next: { onClick: () => submit(local.chains) },
    };
  },

  apply(s) {
    if (!s.changes.length) {
      return {
        body: [note('ok', 'Your settings are in place. Nothing needs a restart.')],
        next: { onClick: () => submit() },
      };
    }
    return {
      body: [
        h('p', {}, 'These need one restart to take effect. It takes about a minute, and this page carries on by itself.'),
        h('div.wz-list', {}, s.changes.map(c => h('div', {}, h('span.v', { style: 'text-align:left' }, c)))),
      ],
      next: { label: 'Restart and apply', onClick: () => submit() },
    };
  },

  lightcheck(s) {
    const chain = s.chains[local.idx ?? 0];
    if (!chain) return { body: [note('ok', 'All checked.')], next: { onClick: () => submit() } };
    const count = `${(local.idx ?? 0) + 1} of ${s.chains.length}`;
    const head = [
      h('div.wz-center', { style: 'display:flex;flex-direction:column;gap:12px;align-items:center' },
        h('span.wz-big-icon', {}, icon(I.cabinet, { size: 52, color: '#7a3db0', width: 1.8 })),
        h('div.wz-kicker', {}, `Look at your jukebox · ${count}`),
        h('p', { style: 'font-size:22px;font-weight:800;color:#2a1640' }, chain.ask),
        h('p', {}, chain.detail)),
    ];
    if (local.phase === 'no') {
      return {
        body: [...head,
          note('warn', 'Things to check:', h('ul', {}, chain.tips.map(t => h('li', {}, t)))),
          h('div.wz-row', {},
            h('button.wz-ghost', { type: 'button', onClick: () => showChain(local.idx) }, 'Show it again'),
            h('button.wz-ghost', { type: 'button', onClick: async () => { await act('absent', { chain: chain.id }); nextChain(); } }, 'This light isn\'t connected'),
            h('button.wz-ghost', { type: 'button', onClick: async () => { await act('answer', { chain: chain.id, ok: false }); nextChain(); } }, 'Continue anyway'))],
        next: null,
      };
    }
    return {
      body: [...head,
        local.phase === 'starting' ? h('p', { class: 'wz-center' }, 'Lighting it up…') : null,
        h('div', { style: 'display:flex;flex-direction:column;gap:10px' },
          h('button.wz-next', { type: 'button', style: 'flex:none', disabled: local.phase !== 'asking', onClick: () => answerChain(true) }, 'Yes, they look right'),
          h('button.wz-ghost', { type: 'button', disabled: local.phase !== 'asking', onClick: () => answerChain(false) }, 'No, something\'s off'))],
      next: null,
    };
  },

  loadtest(s) {
    const t = s.test;
    const body = [h('p', {}, s.chains.length
      ? 'For 30 seconds every light goes as bright as it\'s allowed, to make sure the power supply keeps up. Your jukebox will look very bright.'
      : 'For 30 seconds the Pi works as hard as it can, to make sure the power supply keeps up.')];
    if (!t) {
      body.push(h('button.wz-next', { type: 'button', style: 'flex:none', disabled: local.acting === 'start', onClick: () => act('start') }, 'Start the power check'));
      return { body, next: null };
    }
    const elapsed = Math.min(30, t.samples.length);
    body.push(h('div.wz-bar', { role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '30', 'aria-valuenow': String(elapsed) },
      h('div', { style: `width:${(elapsed / 30) * 100}%` })));
    body.push(h('div.wz-stats', {},
      s.pi5 ? stat('Lowest voltage', t.min_volts ? `${t.min_volts.toFixed(2)} V` : '–') : stat('Power', t.throttled & 1 ? 'Low' : 'Good'),
      stat('Temperature', t.max_temp ? `${Math.round(t.max_temp)} °C` : '–')));
    if (t.running) return { body, next: null };
    if (t.passed) body.push(note('ok', 'Your power supply keeps up easily.'));
    else {
      body.push(note('bad', 'The power dipped too low.', h('ul', {},
        h('li', {}, 'Turn the supply up to 5.2–5.25 V at the board: the small adjuster on the power supply, clockwise.'),
        h('li', {}, 'Use shorter, thicker wires to the Pi\'s pins 2 and 4.'))));
      body.push(h('div.wz-row', {},
        h('button.wz-ghost', { type: 'button', onClick: () => act('start') }, 'Run it again'),
        h('button.wz-ghost', { type: 'button', onClick: () => submit({ skip: true }) }, 'Continue anyway')));
      return { body, next: null };
    }
    return { body, next: { onClick: () => submit() } };
  },

  sound(s) {
    const body = [h('div.wz-checks', {},
      check(s.dac, 'The sound card (DAC) is found'),
      check(s.realtime, 'The audio runs at high priority'),
      check(s.clock, 'Steady playback is set up'))];
    if (!s.dac || !s.realtime || !s.clock) body.push(note('warn', 'Something in the audio setup is missing. Music may still play, but it can pop or cut out.'));
    if (!local.played) {
      body.push(h('p', {}, 'Next, the jukebox plays a short chime at a low volume (5%).'));
      body.push(h('button.wz-next', { type: 'button', style: 'flex:none', disabled: local.acting === 'play', onClick: async () => {
        local.playedAt = Date.now();
        if (await act('play')) { local.played = true; render(); }
      } }, 'Play the chime'));
      return { body, next: null };
    }
    if (local.heard === undefined) {
      body.push(h('p', { style: 'font-size:22px;font-weight:800;color:#2a1640' }, 'Did you hear the chime?'));
      body.push(h('div.wz-row', {},
        h('button.wz-next', { type: 'button', onClick: () => { local.heard = true; render(); } }, 'Yes'),
        h('button.wz-ghost', { type: 'button', onClick: () => { local.heard = false; render(); } }, 'No')));
      body.push(h('button.wz-ghost', { type: 'button', onClick: () => act('play') }, 'Play it again'));
      return { body, next: null };
    }
    if (local.heard) body.push(note('ok', 'Sound works. Turn the knob to set a comfortable volume.'));
    else {
      body.push(note('warn', 'Things to check:', h('ul', {},
        h('li', {}, 'The speakers\' wires and the amplifier\'s power.'),
        h('li', {}, 'Turn the knob up a little and play it again.'))));
      body.push(h('button.wz-ghost', { type: 'button', onClick: () => { local.heard = undefined; act('play'); } }, 'Play it again'));
    }
    return { body, next: { onClick: () => submit({ heard: local.heard }) } };
  },

  screen(s) {
    return {
      body: [
        h('p', {}, 'Make sure the picture fits inside the cabinet\'s glass, and choose when the screen sleeps.'),
        note('info', 'Turn it, fit the edges and set the sleep time on the Displays page of the admin.',
          h('div', { style: 'margin-top:8px' }, h('a', { href: '/#/displays', target: '_blank', rel: 'noopener' }, 'Open Displays'))),
      ],
      next: { onClick: () => submit() },
      skip: true,
    };
  },

  controls(s) {
    if (s.phase === 'teach') return controlsTeach(s);
    if (s.phase === 'try') return controlsTry(s);
    return controlsFormat(s);
  },

  sources(s) {
    return {
      body: [
        h('p', {}, 'Where your music comes from. Set up one now, or more later in the admin.'),
        h('div.wz-choices', {},
          sourceCard(I.bt, 'A phone over Bluetooth', s.bluetooth ? `${plural(s.bluetooth, 'phone')} paired` : 'Play from any phone, like a car stereo',
            'Add a phone', 'bluetooth', 'The jukebox is ready to pair. On your phone, open Settings › Bluetooth and tap the jukebox.'),
          sourceCard(I.phone, 'The Juk3box Helper app', s.app ? `${plural(s.app, 'phone')} paired` : 'Your Apple Music library on the board',
            'Let the app pair', 'app', 'Open Juk3box Helper on your iPhone, on the same Wi-Fi. A code shows on the cabinet to type in.'),
          h('div.wz-choice', {}, h('span.wz-icon', {}, icon(I.usb, { color: '#5b2491' })),
            h('span', {}, h('span.t', {}, 'A USB drive'),
              h('span.s', {}, s.drives ? `${plural(s.drives, 'drive')} plugged in` : 'Plug in a drive with music on it: its songs join the board.')))),
      ],
      next: { onClick: () => submit() },
      skip: true,
    };
  },

  lastfm(s) {
    local.key ??= '';
    const without = h('ul', {},
      h('li', {}, 'The board\'s Top 100 stays empty.'),
      h('li', {}, 'The light show picks its look from how loud and busy a song is, not from its genre.'),
      s.weathermix ? h('li', {}, 'Weather Mix picks songs by genre, not by mood.') : null);
    return {
      body: [
        h('p', {}, 'Last.fm knows what millions of listeners play. With a free key from Last.fm, your jukebox fills the board\'s Top 100 '
          + 'and gives each song\'s light show a look that fits its genre.'),
        s.set ? note('ok', `A key is saved (it ends in ${s.ends}). Press Next to keep it, or paste a new one.`) : null,
        h('p', { style: 'font-weight:800;margin-bottom:0' }, 'Get your key (about 3 minutes):'),
        h('ol.wz-howto', {},
          h('li', {}, h('a', { href: s.get_key, target: '_blank', rel: 'noopener' }, 'Open Last.fm\'s API page'),
            '. Sign in, or tap Sign up to make a free account first.'),
          h('li', {}, 'Fill in the form: your email for Contact email, ', h('b', {}, 'Juk3box'), ' for Application name and ',
            h('b', {}, 'My jukebox'), ' for Application description. Leave Callback URL and Application homepage empty.'),
          h('li', {}, 'Tap Submit. Last.fm shows an API key and a shared secret.'),
          h('li', {}, 'Copy the ', h('b', {}, 'API key'), ' (32 letters and numbers). Not the shared secret.'),
          h('li', {}, 'Come back here and paste it below.')),
        h('div.wz-field', {}, h('label.wz-label', { for: 'lfkey' }, 'Last.fm API key'),
          h('input.wz-input', { id: 'lfkey', type: 'text', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false',
            autocomplete: 'off', placeholder: s.set ? `Saved key ends in ${s.ends}` : 'Paste it here', value: local.key,
            onInput: e => { local.key = e.target.value; } })),
        note('warn', 'Without a key:', without, h('div', { style: 'margin-top:6px' }, 'You can add one later on the admin\'s Setup page.')),
      ],
      next: { label: s.set ? 'Next' : 'Save key', onClick: () => submit({ key: local.key.trim() }) },
      skip: true,
    };
  },

  wholehome(s) {
    local.speaker ??= s.whole_home?.enabled ? s.whole_home.airplay : null;
    return {
      body: [
        h('p', {}, 'Play along on an AirPlay speaker in the house. The house follows the cabinet\'s volume knob.'),
        s.caution ? note('warn', s.caution.why) : null,
        s.speakers.length ? h('div.wz-choices', {},
          s.speakers.map(sp => choice({ title: sp, kind: 'radio', on: local.speaker === sp, onClick: () => { local.speaker = sp; render(); } })),
          choice({ title: 'Not now', kind: 'radio', on: local.speaker === null, onClick: () => { local.speaker = null; render(); } }))
          : note('info', 'No AirPlay speakers found on your network.'),
      ],
      next: { onClick: () => submit({ speaker: local.speaker }) },
      skip: true,
    };
  },

  suggestions(s) {
    local.on ??= new Set();
    return {
      body: [
        h('p', {}, `Your Pi runs better without these. They start off; you can turn any on later.`),
        s.items.map(item => h('label.wz-switch', {},
          h('input', { type: 'checkbox', checked: local.on.has(item.id), onChange: e => {
            if (e.target.checked) local.on.add(item.id); else local.on.delete(item.id);
          } }),
          h('span', {}, h('span', { style: 'display:block;font-weight:800;font-size:17px' }, `Keep ${item.name} on`),
            h('span', { style: 'display:block;font-size:15px;color:#5d5470;line-height:1.45;margin-top:3px' },
              item.why, item.cost ? ` It uses ${item.cost}.` : '')))),
      ],
      next: { onClick: () => submit({ on: [...local.on] }) },
    };
  },

  finish(s) {
    return {
      body: [
        h('p', {}, 'Here\'s what we set up. You can change any of it later in the admin.'),
        h('div.wz-list', {}, s.summary.map(r => h('div', {}, h('span.k', {}, r.k), h('span.v', {}, r.v)))),
      ],
      next: { label: 'Finish', onClick: () => submit() },
    };
  },
};

// Setup mode: the jukebox's own hotspot. Pick the home network and join it.
// Joining drops the hotspot, so this phone loses the jukebox for a moment:
// say beforehand where to find it again.
function hotspotWifi(s) {
  const join = s.join;
  if (local.joining || join?.state === 'joining') {
    const ssid = local.joining || join?.ssid;
    return {
      body: [
        h('div.wz-center', { style: 'display:flex;flex-direction:column;gap:14px;align-items:center' },
          h('div.wz-spin', { style: 'width:36px;height:36px;border-width:4px' }),
          h('p', { style: 'font-size:22px;font-weight:800;color:#2a1640' }, `Joining ${ssid}…`)),
        note('info', 'This phone loses the jukebox for a moment while it joins.', h('ol', {},
          h('li', {}, `Put this phone back on ${ssid}.`),
          h('li', {}, 'Open ', h('b', {}, s.address.replace(/^http:\/\//, '')), ' to carry on, or scan the code on the cabinet.'))),
        h('p', {}, 'If it can\'t join, the jukebox\'s own Wi-Fi comes back and this page says why.'),
      ],
      next: null,
      back: false,
    };
  }
  local.ssid ??= SUGGESTED_SSID && s.networks.some(n => n.ssid === SUGGESTED_SSID) ? SUGGESTED_SSID : null;
  local.password ??= '';
  const picked = s.networks.find(n => n.ssid === local.ssid);
  const other = local.ssid === '';
  const needsPassword = other || picked?.secure;
  const bars = sig => sig >= 70 ? 'Strong' : sig >= 45 ? 'Good' : 'Weak';
  const go = async () => {
    const ssid = other ? (local.otherName || '').trim() : local.ssid;
    if (!ssid) { local.error = 'Pick a network.'; return render(); }
    local.joinId = Math.random().toString(36).slice(2, 10);
    const out = await act('join', { ssid, password: local.password, hidden: other, id: local.joinId });
    if (out?.joining) {
      local.joining = ssid;
      render();
      // Inside the Juk3box Helper app: it lets go of the jukebox's Wi-Fi and
      // finds it again at its address on the owner's network.
      window.webkit?.messageHandlers?.juk3box?.postMessage({ event: 'joining', ssid, address: s.address });
    }
  };
  return {
    body: [
      h('p', {}, 'Pick your home Wi-Fi. The jukebox uses it for cover art, the weather and your phones.'),
      join?.state === 'failed' && join.id && join.id === local.failedId ? note('bad', join.reason) : null,
      h('div.wz-choices', {},
        s.networks.map(n => choice({ title: n.ssid, sub: `${bars(n.signal)} signal${n.secure ? '' : ' · open'}`, kind: 'radio',
          on: local.ssid === n.ssid, onClick: () => { local.ssid = n.ssid; render(); } })),
        choice({ title: 'Another network', sub: 'A hidden one, or one not listed', kind: 'radio',
          on: other, onClick: () => { local.ssid = ''; render(); } })),
      s.networks.length ? null : note('warn', 'No networks found yet. Look again, or type its name under Another network.'),
      other ? h('div.wz-field', {}, h('label.wz-label', { for: 'ssid' }, 'Network name'),
        h('input.wz-input', { id: 'ssid', type: 'text', autocapitalize: 'off', autocorrect: 'off', value: local.otherName || '',
          onInput: e => { local.otherName = e.target.value; } })) : null,
      needsPassword ? h('div.wz-field', {}, h('label.wz-label', { for: 'wpw' }, 'Wi-Fi password'),
        h('input.wz-input', { id: 'wpw', type: 'password', autocomplete: 'off', value: local.password,
          onInput: e => { local.password = e.target.value; } })) : null,
      h('button.wz-ghost', { type: 'button', disabled: local.acting === 'scan', onClick: () => act('scan') },
        local.acting === 'scan' ? [h('span.wz-spin'), 'Looking…'] : 'Look again'),
    ],
    next: { label: 'Join', onClick: go, disabled: local.ssid === null || local.ssid === undefined },
  };
}

// -- The controls step (docs/Controls Setup — Plan.md) ---------------------------------

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const KEYPAD_DIGITS = '1234567890';

// The step's own actions answer with the step's view: keep the frame's parts.
function controlsAct(name, data, { quiet = true } = {}) {
  return act(name, data, { quiet }).then(out => {
    if (out && out.phase) {
      const { id, title, skippable, back } = view.step;
      view.step = { ...out, id, title, skippable, back };
      render();
    }
    return out;
  });
}

let previewTimer = null;
function controlsPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const r = await api('POST', '/api/wizard/controls/preview', { spec: local.spec });
    local.preview = r.ok ? r.data : null;
    local.previewError = r.ok ? null : r.data.error;
    render();
  }, 250);
}

function setSpec(spec) {
  local.spec = spec;
  controlsPreview();
  render();
}

function toggleIn(str, ch, order) {
  const set = new Set(str);
  if (set.has(ch)) set.delete(ch); else set.add(ch);
  return [...order].filter(c => set.has(c)).join('');
}

function keyGrid(chars, chosen, onToggle) {
  return h('div.wz-keys', {}, [...chars].map(ch => h(`button.wz-key${chosen.includes(ch) ? '.on' : ''}`,
    { type: 'button', 'aria-pressed': chosen.includes(ch) ? 'true' : 'false', onClick: () => onToggle(ch) }, ch)));
}

function numberField(id, label, value, onChange) {
  return h('div.wz-field', {}, h('label.wz-label', { for: id }, label),
    h('input.wz-input', { id, type: 'number', inputmode: 'numeric', min: '0', value: String(value ?? ''),
      onInput: e => onChange(e.target.value === '' ? null : Number(e.target.value)) }));
}

function miniBoard(p) {
  return h('div.wz-board', {}, p.groups.map(g => h('div.wz-board-group', {},
    h('div.wz-board-label', {}, g.label),
    h('div.wz-board-codes', {}, g.codes.map(c => h('span', {}, c))))));
}

// Step 1: the keypad board (a Pico), when it needs something.
function keypadCard(k) {
  if (!k) return null;
  const busy = local.acting?.startsWith('keypad_');
  const go = (name, label, busyLabel) => h('button.wz-next', { type: 'button', style: 'flex:none', disabled: busy,
    onClick: () => controlsAct(name, {}, { quiet: false }) }, busy && local.acting === name ? [h('span.wz-spin'), busyLabel] : label);
  const look = h('button.wz-ghost', { type: 'button', disabled: busy, onClick: () => load('controls') }, 'Look again');
  switch (k.state) {
    case 'blank':
      return h('div.wz-preview', {}, h('div.wz-preview-say', {}, 'Found a keypad board with nothing on it yet'),
        h('p', {}, 'Install the keypad program on it? It takes about a minute.'),
        go('keypad_install', 'Install the keypad program', 'Installing…'));
    case 'not_pico':
      return note('warn', `That board (${k.board || 'unknown'}) isn't a Raspberry Pi Pico. The keypad program is for the Pico.`);
    case 'maintenance':
      return h('div.wz-preview', {}, h('div.wz-preview-say', {}, 'Your keypad board is ready for its new program'),
        go('keypad_update', `Install version ${k.latest}`, 'Installing…'));
    case 'other':
      return h('div.wz-preview', {}, h('div.wz-preview-say', {}, 'A Pico with other software on it'),
        h('p', {}, 'It may be your own project. Replace what\'s on it with the keypad program?'),
        go('keypad_replace', 'Replace it', 'Installing…'));
    case 'older':
      if (k.signal) {
        return h('div.wz-preview', {}, h('div.wz-preview-say', {}, `Keypad program ${k.latest} is here (yours is ${k.running})`),
          h('p', {}, 'The keypad stops for a few seconds while it updates.'),
          go('keypad_update', 'Update it', 'Updating…'));
      }
      return h('div.wz-preview', {}, h('div.wz-preview-say', {}, `Keypad program ${k.latest} is here`),
        h('p', {}, `Yours is the first version. This once, put it in update mode by hand; from ${k.latest} on it updates by itself.`),
        h('ol.wz-howto', {}, h('li', {}, 'Unplug the keypad board\'s USB cable from the jukebox.'),
          h('li', {}, 'Hold the A button and plug it back in. Keep holding for two seconds.')),
        look);
    case 'current':
      return note('ok', `Keypad program ${k.running}: up to date.`);
    default:
      return null;
  }
}

function controlsFormat(s) {
  local.spec ??= s.format.spec;
  local.preview ??= s.format;
  local.mode ??= s.had?.buttons === false ? 'none' : 'buttons';
  local.knob ??= true;
  local.cancel ??= true;
  const spec = local.spec;
  const letters = spec.kind === 'letters';
  const presetOn = id => JSON.stringify(s.presets.find(x => x.id === id)?.spec) === JSON.stringify(spec);
  const body = [
    keypadCard(s.keypad),
    h('p', {}, 'Tell the jukebox what\'s on your selection buttons, so the song board\'s numbers match what people can press.'),
    h('div.wz-choices.two', {},
      choice({ title: 'The cabinet\'s buttons', sub: 'A keypad, and a knob if it has one', kind: 'radio', on: local.mode === 'buttons',
        onClick: () => { local.mode = 'buttons'; render(); } }),
      choice({ title: 'No buttons yet', sub: 'A plugged-in keyboard, or the phone only', kind: 'radio', on: local.mode === 'none',
        onClick: () => { local.mode = 'none'; render(); } })),
    h('label.wz-label', {}, 'Common layouts'),
    h('div.wz-choices', {}, s.presets.map(pr => choice({ title: pr.label, kind: 'radio', on: presetOn(pr.id),
      onClick: () => setSpec(pr.spec) }))),
    h('label.wz-label', {}, 'Or set your own'),
    h('div.wz-row', {},
      h(`button.wz-ghost${letters ? '.on' : ''}`, { type: 'button', onClick: () => !letters && setSpec({ kind: 'letters', letters: 'ABCDEFGHJK', digits: '1234567890' }) }, 'Letters and numbers'),
      h(`button.wz-ghost${letters ? '' : '.on'}`, { type: 'button', onClick: () => letters && setSpec({ ...s.presets.find(x => x.id === 'rockola').spec }) }, 'Numbers only')),
  ];
  if (letters) {
    body.push(h('p', {}, 'Tick the letters on your buttons. Most jukeboxes skip I and O.'),
      keyGrid(ALPHABET, spec.letters, ch => setSpec({ ...spec, letters: toggleIn(spec.letters, ch, ALPHABET) })),
      h('p', {}, 'And the numbers.'),
      keyGrid(KEYPAD_DIGITS, spec.digits, ch => setSpec({ ...spec, digits: toggleIn(spec.digits, ch, KEYPAD_DIGITS) })));
  } else {
    const places = spec.places || 3;
    body.push(h('div.wz-row', {},
      numberField('cplaces', 'Digits in a selection', places, v => setSpec({ ...spec, places: v, place_digits: Array(Math.max(1, Math.min(4, v || 1))).fill('0123456789') })),
      numberField('cfirst', 'The first number', spec.first, v => setSpec({ ...spec, first: v })),
      numberField('clast', 'The last number', spec.last, v => setSpec({ ...spec, last: v }))),
    h('details.wz-more', {}, h('summary', {}, 'Some places use fewer digits?'),
      h('p', {}, 'Some jukeboxes use only 1–8 in the last place, say. Untick the digits a place never uses.'),
      (spec.place_digits || []).map((pd, i) => h('div.wz-field', {}, h('span.wz-label', {}, `Place ${i + 1}`),
        keyGrid('0123456789', pd, ch => {
          const next = [...spec.place_digits];
          next[i] = toggleIn(pd, ch, '0123456789');
          setSpec({ ...spec, place_digits: next });
        })))));
  }
  const p = local.preview;
  body.push(h('div.wz-preview', {},
    p ? [h('div.wz-preview-say', {}, p.describe), miniBoard(p),
      h('p', {}, `Compare with your cabinet's own title cards: is the first ${p.first} and the last ${p.last}?`)]
      : note('warn', local.previewError || 'That doesn\'t make a page of songs yet.')));
  if (local.mode === 'buttons') {
    body.push(h('label.wz-label', {}, 'Your cabinet also has'),
      h('div.wz-choices.two', {},
        choice({ title: 'A knob', sub: 'It turns and presses', on: local.knob, onClick: () => { local.knob = !local.knob; render(); } }),
        choice({ title: 'A Cancel button', sub: 'Clears a half-entered pick', on: local.cancel, onClick: () => { local.cancel = !local.cancel; render(); } })));
    if (s.device === 'missing' && !s.keyboards) {
      body.push(note('warn', 'No keypad or keyboard is plugged into the jukebox. Plug one in, then go on.',
        h('div', { style: 'margin-top:6px' }, 'A Pico keypad board with other software on it shows up once you hold its white BOOTSEL button while plugging it in.')));
    }
  } else {
    body.push(note('info', 'A keyboard plugged into the jukebox always works: letters and numbers pick, the arrows and Enter are the knob, Backspace is Cancel, and up and down change the volume.'));
  }
  return {
    body,
    next: local.mode === 'buttons'
      ? { label: 'Teach the buttons', disabled: !p, onClick: () => controlsAct('start', { spec: p.spec, knob: local.knob, cancel: local.cancel }) }
      : { disabled: !p, onClick: () => submit({ no_buttons: true, spec: p.spec }) },
    skip: true,
  };
}

function teachKeys(s) {
  return h('div.wz-keys.teach', {}, s.targets.map(t => h(`span.wz-key.${t.state}${t.kind === 'key' ? '' : '.wide'}`,
    { title: t.state }, t.label)));
}

function controlsTeach(s) {
  const left = s.targets.filter(t => t.state === 'todo' || t.state === 'now').length;
  return {
    body: [
      h('div.wz-center', { style: 'display:flex;flex-direction:column;gap:10px;align-items:center' },
        h('div.wz-kicker', {}, `At the cabinet · ${left} to go`),
        h('p.wz-prompt', {}, s.turning ? 'Counting…' : s.prompt)),
      s.message ? note('warn', s.message) : null,
      s.quick ? note('info', 'Your keypad already sends letters and numbers: press every key, in any order.') : null,
      s.steps_per_click && s.now !== 'knob.cw' ? note('ok', `The knob sends ${plural(s.steps_per_click, 'step')} a click.`) : null,
      teachKeys(s),
      h('div.wz-row', {},
        h('button.wz-ghost', { type: 'button', disabled: !s.can_back, onClick: () => controlsAct('back') }, 'Back one'),
        h('button.wz-ghost', { type: 'button', onClick: () => controlsAct('skip') }, 'This one doesn\'t work'),
        h('button.wz-ghost', { type: 'button', onClick: () => controlsAct('restart') }, 'Start over')),
    ],
    next: null,
  };
}

function controlsTry(s) {
  const t = s.trial || {};
  const picked = t.picked ? `${t.picked.code}: song ${t.picked.n}` : t.typed ? `${t.typed}…` : '–';
  const skipped = s.targets.filter(x => x.state === 'skipped').map(x => x.label);
  return {
    body: [
      h('p', {}, 'Try it at the cabinet: pick a song, turn and press the knob, press Cancel. Nothing plays.'),
      h('div.wz-stats', {},
        stat('Picked', picked),
        stat('Knob', t.clicks ? `${Math.abs(t.clicks)} ${t.clicks > 0 ? '→' : '←'}` : '–'),
        stat('Knob presses', String(t.pressed || 0)),
        stat('Cancel', String(t.cancelled || 0))),
      t.said ? note('warn', t.said) : null,
      s.message ? note('warn', s.message) : null,
      s.bouncy.length ? note('ok', `Fixed bouncy buttons: ${s.bouncy.join(', ')}.`) : null,
      skipped.length ? note('warn', `Not working yet: ${skipped.join(', ')}. Teach them again once they're fixed.`) : null,
      h('button.wz-ghost', { type: 'button', onClick: () => controlsAct('again') }, 'Teach again'),
    ],
    next: { label: 'Looks right', onClick: () => submit() },
  };
}

function check(ok, label) {
  return h(`div.wz-check${ok ? '.yes' : ''}`, {},
    h('span.dot', {}, ok ? icon(I.check, { color: '#fff', size: 16, width: 3.5 }) : null), label);
}

function sourceCard(iconPath, title, sub, button, action, after) {
  const started = local.started?.[action];
  return h('div.wz-choice', { style: 'flex-wrap:wrap' },
    h('span.wz-icon', {}, icon(iconPath, { color: '#5b2491' })),
    h('span', { style: 'flex:1 1 200px' }, h('span.t', {}, title), h('span.s', {}, started ? after : sub)),
    started ? null : h('button.wz-ghost', { type: 'button', onClick: async () => {
      if (await act(action)) { local.started = { ...(local.started || {}), [action]: true }; render(); }
    } }, button));
}

async function showChain(idx) {
  local.idx = idx;
  local.phase = 'starting';
  render();
  const chain = view.step.chains[idx];
  if (!chain) return;
  const out = await act('show', { chain: chain.id }, { quiet: true });
  local.shownAt = Date.now();
  local.phase = out ? 'asking' : 'no';
  render();
}

async function answerChain(yes) {
  if (local.phase !== 'asking') return;
  local.phase = 'answered';
  const chain = view.step.chains[local.idx];
  if (!yes) { local.phase = 'no'; return render(); }
  await act('answer', { chain: chain.id, ok: true }, { quiet: true });
  nextChain();
}

function nextChain() {
  const next = (local.idx ?? 0) + 1;
  if (next < view.step.chains.length) showChain(next);
  else submit();
}

load(ONLY_STEP);
