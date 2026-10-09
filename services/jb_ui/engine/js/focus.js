// The knob (docs/theme-format.md, "The knob"). Turning moves a highlight
// through the knob stops; clicking acts on the highlighted one. The order
// is worked out from the layout each time, so any theme's arrangement
// steers correctly.
//
// Blocks make an element a stop with makeStop(el, key, onClick). Regions
// marked with a layout's "focus" setting group stops together.

export const REST_TIMEOUT_MS = 12000;  // idle -> back to the resting stop
export const MODE_TIMEOUT_MS = 4000;   // a knob mode (e.g. volume) ends after this idle

let current = null;        // the highlighted stop element
let currentKey = null;     // its key, to find it again after a re-render
let mode = null;           // {name, rotate(dir), exit()} while the knob is repurposed
let restTimer = null;
let modeTimer = null;

export function makeStop(el, key, onClick) {
  el.dataset.focusStop = key;
  el.jbClick = onClick;
}

// Stops in knob order, clockwise, and the resting stop.
export function ring() {
  const groups = [...document.querySelectorAll('[focus]')];
  const restGroup = groups.find(g => g.getAttribute('focus-flow') === 'down-from-rest');
  if (!restGroup) {
    const all = groups.flatMap(stopsIn);
    return { stops: all, rest: all[0] || null };
  }
  // The nav: its first stop rests. Clockwise from there goes into the
  // content; counter-clockwise walks down the nav.
  const nav = stopsIn(restGroup);
  const content = groups.filter(g => g !== restGroup && !restGroup.contains(g)).flatMap(stopsIn);
  return { stops: [nav[0], ...content, ...nav.slice(1).reverse()].filter(Boolean), rest: nav[0] || null };
}

function stopsIn(group) {
  const stops = [...group.querySelectorAll('[data-focus-stop]')]
    .map(el => ({ el, r: el.getBoundingClientRect() }))
    .filter(({ r }) => r.width > 0 && r.height > 0);
  // Reading order: rows top to bottom, left to right within a row. Two
  // stops share a row when their vertical centers are within half a
  // stop's height of each other.
  stops.sort((a, b) => {
    const dy = (a.r.top + a.r.height / 2) - (b.r.top + b.r.height / 2);
    if (Math.abs(dy) < Math.min(a.r.height, b.r.height) / 2) return a.r.left - b.r.left;
    return dy;
  });
  return stops.map(s => s.el);
}

function setCurrent(el) {
  if (current && current !== el) delete current.dataset.focused;
  current = el;
  currentKey = el?.dataset.focusStop ?? null;
  if (el) el.dataset.focused = '';
}

// After the page changes (a new screen), keep the highlight on the same
// stop if it still exists, else go to rest.
export function refocus() {
  const { stops, rest } = ring();
  const same = currentKey && stops.find(el => el.dataset.focusStop === currentKey);
  setCurrent(same || rest);
}

export function goToRest() {
  setCurrent(ring().rest);
}

export function rotate(steps) {
  if (mode) {
    mode.rotate(steps);
    armModeTimer();
    return;
  }
  const { stops } = ring();
  if (!stops.length) return;
  let i = stops.indexOf(current);
  if (i < 0) i = 0;
  const n = stops.length;
  setCurrent(stops[(((i + steps) % n) + n) % n]);
  armRestTimer();
}

export function click() {
  if (mode) {
    exitMode();  // a click ends a knob mode early
    return;
  }
  if (!current?.isConnected) refocus();
  current?.jbClick?.();
  armRestTimer();
}

// Repurpose the knob, e.g. turning changes the volume. Ends on a click or
// after MODE_TIMEOUT_MS without turning.
export function enterMode(name, handlers) {
  if (mode) exitMode();
  mode = { name, ...handlers };
  document.documentElement.dataset.mode = name;
  clearTimeout(restTimer);
  armModeTimer();
}

export function exitMode() {
  if (!mode) return;
  const ended = mode;
  mode = null;
  clearTimeout(modeTimer);
  document.documentElement.dataset.mode = 'select';
  ended.exit?.();
  armRestTimer();
}

export function currentMode() {
  return mode?.name || 'select';
}

function armRestTimer() {
  clearTimeout(restTimer);
  restTimer = setTimeout(() => { if (!mode) goToRest(); }, REST_TIMEOUT_MS);
}

function armModeTimer() {
  clearTimeout(modeTimer);
  modeTimer = setTimeout(exitMode, MODE_TIMEOUT_MS);
}
