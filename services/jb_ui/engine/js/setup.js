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
  layer.replaceChildren(s.restarting && s.step === 'apply' ? restarting() : screen(s));
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

function screen(s) {
  const wrap = el('div', 'jb-setup-screen');

  const main = el('div', 'jb-setup-main');
  main.append(logo());
  if (s.n) main.append(el('div', 'jb-setup-kicker', `Step ${s.n} of ${s.of}`));
  main.append(el('div', 'jb-setup-title', s.title));
  if (s.say) main.append(el('div', 'jb-setup-say', s.say));
  if (s.live) main.append(live(s.live));
  if (s.ask) main.append(ask(s.step, s.ask));

  const side = el('div', 'jb-setup-side');
  const join = el('div', 'jb-setup-join');
  join.append(el('div', 'jb-setup-label', 'Set up from your phone or computer'));
  if (s.qr?.length) join.append(qr(s.qr));
  join.append(el('div', 'jb-setup-address', s.address.replace(/^http:\/\//, '')));
  side.append(join);
  const list = el('ol', 'jb-setup-steps');
  for (const step of s.steps || []) {
    const li = el('li', step.now ? 'now' : step.done ? 'done' : '', step.short);
    list.append(li);
  }
  side.append(list);

  wrap.append(main, side);
  return wrap;
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
