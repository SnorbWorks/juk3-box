// Settings > Displays (the display block): the picture area, so the
// screen's contents fit the window in the cabinet's glass, the rotation,
// and screen sleep. jb-display (services/jb_display/) keeps the settings,
// turns the screen and puts it to sleep; the picture area is fitted here.
//
// Fitting: the background still fills the whole panel; the page itself
// (#jb-root) and the popups are placed inside the edges and scaled down
// with CSS zoom, so a theme sized in vh/vw/vmin still fits as it was
// designed to -- zoom scales those units too. The scale is the tighter of
// the two directions; the other one simply gets a little more room.
//
// Markup: engine blocks display, popup-edge and popup-choices (rows are
// phone-row).

import { bind, fromTemplate } from './bind.js';
import { PREVIEW, publish } from './bus.js';
import { enterMode, focusKey, makeStop, refocus } from './focus.js';
import { closePopup, openPopup, updatePopup } from './popup.js';
import { store } from './store.js';

const STEP_PX = 4;            // per knob detent
const SEND_EVERY_MS = 120;    // jb-display saves and shares every change
const EDGE_MODE_MS = 20000;   // lining an edge up takes a while
const KEEP_ROTATION_S = 15;
const SIDES = ['top', 'bottom', 'left', 'right'];
const SIDE_LABELS = { top: 'Top edge', bottom: 'Bottom edge', left: 'Left edge', right: 'Right edge' };
const ROTATIONS = [
  { value: 0, label: 'Normal' },
  { value: 90, label: 'Turned right' },
  { value: 180, label: 'Upside down' },
  { value: 270, label: 'Turned left' },
];
const SLEEPS = [0, 5, 10, 15, 30, 60, 120].map(value => ({ value, label: sleepLabel(value) }));

let templates = {};
let guide = null;
let movingSide = null;        // the side being moved with the knob, for the guide
let moving = null;            // its edges while the knob moves it, ahead of jb-display
let fitted = '';              // the last fit written, so an unchanged one isn't rewritten

const command = (action, extra = {}) => {
  if (!PREVIEW) publish('jukebox/jb-display/cmd', { action, ...extra });
};
const state = () => store.get('display') || null;

function sleepLabel(minutes) {
  if (!minutes) return 'Never';
  if (minutes % 60 === 0) return minutes === 60 ? 'After 1 hour' : `After ${minutes / 60} hours`;
  return `After ${minutes} minutes`;
}

export function initDisplay(blockTemplates) {
  templates = blockTemplates;
  // The admin's preview is a frame of its own size: no fitting there.
  if (PREVIEW) return;
  guide = document.createElement('div');
  guide.id = 'jb-display-guide';
  guide.hidden = true;
  document.body.append(guide);
  // The admin's Displays page can show the guide too, while the owner
  // types numbers in from a phone.
  store.watch(guide, 'display', () => { fit(); showGuide(); });
  addEventListener('resize', fit);
}

// Place the page and the popups inside the picture area.
function fit() {
  const inset = moving || state()?.inset || {};
  const [top, bottom, left, right] = SIDES.map(s => Math.max(0, inset[s] || 0));
  const W = innerWidth;
  const H = innerHeight;
  const w = Math.max(1, W - left - right);
  const h = Math.max(1, H - top - bottom);
  const key = [top, bottom, left, right, W, H].join(',');
  if (key === fitted) return;
  fitted = key;
  const whole = !top && !bottom && !left && !right;
  // Zoom scales the element's own position and size too, so they're given
  // in its scaled units.
  const z = Math.min(w / W, h / H);
  const placed = whole ? '' : `position: fixed; left: ${left / z}px; top: ${top / z}px; right: auto; bottom: auto; `
    + `width: ${w / z}px; height: ${h / z}px; zoom: ${z};`;
  for (const el of ['jb-root', 'jb-popup-layer', 'jb-setup'].map(id => document.getElementById(id))) {
    if (el) el.style.cssText = placed;
  }
  guide.style.cssText = whole ? '' : `left: ${left}px; top: ${top}px; right: auto; bottom: auto; width: ${w}px; height: ${h}px;`;
}

function showGuide() {
  const on = !!movingSide || !!state()?.guide;
  if (guide.hidden === on) guide.hidden = !on;
  const side = movingSide || '';
  if ((guide.dataset.side || '') !== side) guide.dataset.side = side;
}

function pictureSize(d) {
  const o = d.output;
  if (!o?.width) return null;
  const [w, h] = d.rotation % 180 ? [o.height, o.width] : [o.width, o.height];
  return [w - d.inset.left - d.inset.right, h - d.inset.top - d.inset.bottom];
}

function note(d) {
  if (!d) return 'Waiting for the display service…';
  if (!d.sway) return 'Can\'t reach the screen right now.';
  const o = d.output;
  const panel = o ? `${o.model || o.name}, ${o.width} × ${o.height} at ${Math.round(o.refresh)} Hz.` : '';
  return d.sleep_minutes ? `${panel} Any button or music wakes it.` : panel;
}

function areaNote(d) {
  const size = d && pictureSize(d);
  const fit = 'Pull each edge in until it lines up with the window in the glass.';
  return size ? `${fit} The picture is ${size[0]} × ${size[1]}.` : fit;
}

export function fillDisplay(el) {
  el.append(fromTemplate(templates.display));
  const actions = {
    ...Object.fromEntries(SIDES.map(side => [side, () => moveEdge(side)])),
    reset: () => command('inset_reset'),
    rotation: pickRotation,
    sleep: pickSleep,
  };
  for (const stop of el.querySelectorAll('[data-stop]')) {
    const name = stop.dataset.stop;
    makeStop(stop, `display:${name}`, () => actions[name]?.());
  }
  const render = () => {
    const d = state();
    const inset = d?.inset || {};
    bind(el, {
      ...Object.fromEntries(SIDES.map(side => [side, d ? `${inset[side]} px` : ''])),
      pulledIn: SIDES.some(side => inset[side] > 0),
      areaNote: areaNote(d),
      rotation: d ? ROTATIONS.find(r => r.value === d.rotation)?.label : '',
      sleep: d ? sleepLabel(d.sleep_minutes) : '',
      note: note(d),
    });
  };
  store.watch(el, 'display', render);
  render();
}

// Turning the knob moves one edge in (clockwise) or out, live, with the
// guide showing where the picture's edge is. A click ends it.
function moveEdge(side) {
  const d = state();
  if (!d) return;
  let px = d.inset[side];
  const most = d.limits?.[side] ?? 0;
  let sent = px;
  let timer = null;
  const send = () => {
    if (px !== sent) command('inset', { side, px });
    sent = px;
  };
  const show = () => updatePopup('edge', {
    title: SIDE_LABELS[side],
    px,
    hint: 'Turn right to bring this edge in, left to push it out. Click when it lines up with the glass.',
  });
  movingSide = side;
  if (guide) showGuide();
  openPopup('edge', {});
  show();
  enterMode('display-edge', {
    timeoutMs: EDGE_MODE_MS,
    rotate(steps) {
      px = Math.max(0, Math.min(most, px + steps * STEP_PX));
      show();
      // On screen at once; jb-display hears of it a moment later.
      if (guide) {
        moving = { ...(state()?.inset || d.inset), [side]: px };
        fit();
      }
      // The first step goes at once; the rest at most every SEND_EVERY_MS.
      if (timer) return;
      send();
      timer = setTimeout(() => { timer = null; send(); }, SEND_EVERY_MS);
    },
    exit() {
      clearTimeout(timer);
      send();
      movingSide = null;
      // Keep the edges as moved until jb-display's state has caught up
      // (else the page would jump back for a moment).
      setTimeout(() => { moving = null; if (guide) fit(); }, 1500);
      if (guide) showGuide();
      closePopup('edge');
    },
  });
}

// A list to pick one from (rows are phone-row; the current one is marked).
function choose(title, options, current, back, onPick) {
  const done = () => { closePopup(); focusKey(back); };
  openPopup('choices', { title }, { stops: { close: done } });
  const holder = document.querySelector('.jb-popup-frame[data-popup="choices"] [data-slot="choices"]');
  holder?.replaceChildren(...options.map((option, i) => {
    const row = fromTemplate(templates['phone-row']).firstElementChild;
    bind(row, { name: option.label, status: option.value === current ? 'Now' : '' });
    row.toggleAttribute('data-connected', option.value === current);
    makeStop(row, `popup:choices:${i}`, () => {
      done();
      if (option.value !== current) onPick(option.value);
    });
    return row;
  }));
  refocus();
  focusKey(`popup:choices:${Math.max(options.findIndex(o => o.value === current), 0)}`);
}

function pickSleep() {
  const d = state();
  if (!d) return;
  choose('Screen sleep', SLEEPS, d.sleep_minutes, 'display:sleep', minutes => command('sleep', { minutes }));
}

// A wrong rotation leaves the screen hard to read, so a new one goes back
// by itself unless it's kept, like a computer's display settings.
function pickRotation() {
  const d = state();
  if (!d) return;
  const before = d.rotation;
  choose('Rotation', ROTATIONS, before, 'display:rotation', degrees => {
    command('rotation', { degrees });
    let left = KEEP_ROTATION_S;
    const message = () => `Going back to ${ROTATIONS.find(r => r.value === before).label} in ${left} s.`;
    let decided = false;
    const finish = keep => {
      decided = true;
      clearInterval(timer);
      if (!keep) command('rotation', { degrees: before });
      closePopup('confirm');
      focusKey('display:rotation');
    };
    const timer = setInterval(() => {
      left -= 1;
      if (left <= 0) return finish(false);
      updatePopup('confirm', { message: message() });
    }, 1000);
    openPopup('confirm', { title: 'Keep this rotation?', message: message(), yesLabel: 'Keep' }, {
      stops: { yes: () => finish(true), no: () => finish(false) },
      // Closed some other way (another popup): not kept.
      onClose: () => { if (!decided) finish(false); },
    });
    focusKey('popup:confirm:yes');
  });
}
