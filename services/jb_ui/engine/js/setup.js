// The cabinet during setup (docs/Setup Wizard — Plan.md, "The look"): the
// logo, where to open setup (the address and a QR code), the progress, and
// what to look at right now. jb-admin's wizard publishes it on
// jukebox/jb-admin/setup while setup is open on a phone or computer; the
// questions it asks there (do the lights look right? did you hear it?) can
// be answered here with the knob, on jukebox/jb-admin/setup-answer.
//
// Under the popups, so a pairing code shown during the Sources step is seen.

import { publish } from './bus.js';
import { goToRest, makeStop, refocus } from './focus.js';
import { store } from './store.js';

let layer = null;
let answered = null;   // the ask id the knob answered, so it isn't asked again

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

export function initSetup() {
  layer = el('div');
  layer.id = 'jb-setup';
  layer.hidden = true;
  document.body.append(layer);
  store.watch(layer, 'setup', show);
}

function show(s) {
  const on = !!s?.active;
  if (!on) {
    if (!layer.hidden) {
      layer.hidden = true;
      delete layer.dataset.modal;
      layer.replaceChildren();
      goToRest();
    }
    return;
  }
  const opening = layer.hidden;
  layer.hidden = false;
  // The knob stays here while setup shows: nothing behind it gets picked.
  layer.dataset.modal = '';
  layer.replaceChildren(s.waiting ? waiting(s) : s.restarting && s.step === 'apply' ? restarting() : screen(s));
  refocus();
  if (opening) answered = null;
}

function logo() {
  const box = el('div', 'jb-setup-logo');
  const img = el('img');
  img.src = '/engine/setup-logo.png';
  img.alt = 'Juk3box';
  box.append(img);
  return box;
}

function restarting() {
  const box = el('div', 'jb-setup-restart');
  box.append(logo(), el('div', 'jb-setup-spin'), el('div', 'jb-setup-title', 'Restarting to apply your settings'),
    el('div', 'jb-setup-say', 'Back in about a minute.'));
  return box;
}

// A new jukebox that just started: nothing to join or open yet.
function waiting(s) {
  const box = el('div', 'jb-setup-restart');
  box.append(logo(), el('div', 'jb-setup-spin'), el('div', 'jb-setup-title', s.title), el('div', 'jb-setup-say', s.say));
  return box;
}

function screen(s) {
  const wrap = el('div', 'jb-setup-screen');

  const main = el('div', 'jb-setup-main');
  main.append(logo());
  if (s.n) main.append(el('div', 'jb-setup-kicker', `Step ${s.n} of ${s.of}`));
  main.append(el('div', 'jb-setup-title', s.title));
  if (s.say) main.append(el('div', 'jb-setup-say', s.say));
  if (s.problem) main.append(el('div', 'jb-setup-problem', `⚠ ${s.problem}`));
  if (s.live) main.append(live(s.live));
  if (s.controls) main.append(controls(s.controls));
  if (s.ask) main.append(ask(s.step, s.ask));

  const side = el('div', 'jb-setup-side');
  side.append(s.hotspot ? hotspotJoin(s.hotspot) : addressJoin(s));
  const list = el('ol', 'jb-setup-steps');
  for (const step of s.steps || []) {
    const li = el('li', step.now ? 'now' : step.done ? 'done' : '', step.short);
    list.append(li);
  }
  side.append(list);

  wrap.append(main, side);
  return wrap;
}

function addressJoin(s) {
  const join = el('div', 'jb-setup-join');
  join.append(el('div', 'jb-setup-label', 'Set up from your phone or computer'));
  if (s.qr?.length) join.append(qr(s.qr));
  join.append(el('div', 'jb-setup-address', s.address.replace(/^http:\/\//, '')));
  return join;
}

// Setup mode: join the jukebox's own Wi-Fi first, from the phone's Wi-Fi
// settings, and the setup page then opens by itself. (No "join" QR code:
// iOS joined from one but never opened the sign-in sheet.)
function hotspotJoin(hs) {
  const join = el('div', 'jb-setup-join');
  join.append(el('div', 'jb-setup-label', 'On your phone, open Settings › Wi-Fi and join'));
  const creds = el('div', 'jb-setup-creds');
  for (const [k, v] of [['Wi-Fi', hs.ssid], ['Password', hs.password]]) {
    const row = el('div');
    row.append(el('span', 'k', k), el('span', 'v', v));
    creds.append(row);
  }
  join.append(creds);
  join.append(el('div', 'jb-setup-hint', `Setup opens by itself. If not, go to ${hs.address.replace(/^http:\/\//, '')}`));
  if (hs.why === 'asked') {
    // Started from the power menu: the knob can take it back.
    const cancel = el('div', 'jb-setup-button', 'Cancel');
    makeStop(cancel, 'setup:cancel', () => publish('jukebox/jb-admin/setup-answer', { action: 'stop_hotspot' }));
    join.append(cancel);
  }
  return join;
}

function live(t) {
  const box = el('div', 'jb-setup-live');
  const stat = (k, v) => {
    const d = el('div', 'jb-setup-stat');
    d.append(el('div', 'k', k), el('div', 'v', v));
    return d;
  };
  if (t.min_volts) box.append(stat('Lowest voltage', `${t.min_volts.toFixed(2)} V`));
  if (t.max_temp) box.append(stat('Temperature', `${Math.round(t.max_temp)} °C`));
  if (!t.running && t.passed !== undefined && t.passed !== null) {
    box.append(stat('Result', t.passed ? 'Passed' : 'Too low'));
  }
  return box;
}

// The controls step: each button lights as it's learned, then the try-it.
function controls(c) {
  const box = el('div', 'jb-setup-controls');
  if (c.phase === 'teach' && c.prompt) box.append(el('div', 'jb-setup-prompt', c.prompt));
  if (c.phase === 'try') {
    const t = c.trial || {};
    const picked = t.picked ? `${t.picked.code}: song ${t.picked.n}` : t.typed ? `${t.typed}…` : 'Pick a song';
    box.append(el('div', 'jb-setup-prompt', picked));
    const knob = t.clicks ? `Knob ${Math.abs(t.clicks)} ${t.clicks > 0 ? '→' : '←'}` : '';
    if (knob || t.said) box.append(el('div', 'jb-setup-say', [knob, t.said].filter(Boolean).join(' · ')));
  }
  if (c.message) box.append(el('div', 'jb-setup-problem', c.message));
  const keys = el('div', 'jb-setup-keys');
  for (const k of c.targets || []) keys.append(el('span', `jb-setup-key ${k.state}`, k.label));
  box.append(keys);
  return box;
}

function ask(step, a) {
  const box = el('div', 'jb-setup-ask');
  box.append(el('div', 'jb-setup-q', a.q));
  if (answered === a.id) {
    box.append(el('div', 'jb-setup-say', 'Thanks. Continue on your phone or computer.'));
    return box;
  }
  const row = el('div', 'jb-setup-buttons');
  for (const [key, yes] of [['yes', true], ['no', false]]) {
    const b = el('div', `jb-setup-button${yes ? ' primary' : ''}`, a[key]);
    makeStop(b, `setup:${key}`, () => {
      answered = a.id;
      publish('jukebox/jb-admin/setup-answer', { step, yes });
      show(store.get('setup'));
    });
    row.append(b);
  }
  box.append(row);
  return box;
}

// The QR code, from jb-admin's rows of 1s and 0s, with its quiet border.
function qr(rows) {
  const n = rows.length;
  const quiet = 2;
  const size = n + quiet * 2;
  const canvas = el('canvas', 'jb-setup-qr');
  const scale = 8;
  canvas.width = canvas.height = size * scale;
  const g = canvas.getContext('2d');
  g.fillStyle = '#ffffff';
  g.fillRect(0, 0, canvas.width, canvas.height);
  g.fillStyle = '#2a1640';
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] === '1') g.fillRect((x + quiet) * scale, (y + quiet) * scale, scale, scale);
    }
  });
  return canvas;
}
