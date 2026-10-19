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

// Stops in knob order, clockwise, and the resting stop. While an overlay
// that holds stops is open (e.g. Now Playing), the knob stays inside it.
//
// Working the order out measures every stop, which forces the browser to
// recalculate styles and layout right then: ~60 ms a knob step on the Pi
// (traced 2026-10-14). So it's worked out once and kept until the page
// changes in a way that could change it -- stops added or removed, shown
// or hidden, or the window resized -- not on every step. (Scrolling never
// changes the order: everything in a scrolling area moves together.)
let cachedRing = null;
let watching = false;
let observer = null;
const drop = () => { cachedRing = null; };
// Text changing (the song's elapsed time, twice a second) moves no stop:
// only elements coming or going, or the attributes above, count.
const elementsOnly = records => {
  for (const r of records) {
    if (r.type === 'attributes') return drop();
    for (const n of [...r.addedNodes, ...r.removedNodes]) if (n.nodeType === 1) return drop();
  }
};

function watchForChanges() {
  if (watching) return;
  watching = true;
  observer = new MutationObserver(elementsOnly);
  observer.observe(document.body, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ['hidden', 'data-empty', 'data-shape', 'data-focus-stop', 'data-modal', 'data-cards', 'data-parked'],
  });
  addEventListener('resize', drop);
}

export function ring() {
  watchForChanges();
  // Changes made just now (a popup's buttons, a moment ago in this same
  // task) haven't been reported yet: collect them before trusting the cache.
  elementsOnly(observer.takeRecords());
  if (cachedRing && (!cachedRing.stops.length || cachedRing.stops[0].isConnected)) return cachedRing;
  cachedRing = workOutRing();
  return cachedRing;
}

// The ring, with where each stop sits in the area that scrolls it (for
// reveal), measured now in one go.
function workOutRing() {
  const ring = workOutOrder();
  ring.geo = new Map();
  const boxes = new Map();
  for (const el of ring.stops) {
    const scroller = scrollerOf(el);
    if (!scroller) continue;
    if (!boxes.has(scroller)) {
      const b = scroller.getBoundingClientRect();
      boxes.set(scroller, { box: b, scroll: scroller.scrollTop, max: scroller.scrollHeight - scroller.clientHeight });
      scrolledTo.set(scroller, scroller.scrollTop);
    }
    const { box, scroll, max } = boxes.get(scroller);
    const r = el.getBoundingClientRect();
    ring.geo.set(el, { scroller, top: r.top - box.top + scroll, bottom: r.bottom - box.top + scroll, view: box.height, max });
  }
  return ring;
}

function workOutOrder() {
  const modal = document.querySelector('[data-modal]');
  if (modal) {
    const stops = stopsIn(modal);
    return { stops, rest: stops[0] || null };
  }
  const groups = [...document.querySelectorAll('[focus]')].filter(g => !g.closest('[data-parked]'));
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
  // A group can itself be the stop (the phone badge: one block, one stop).
  const stops = [...(group.matches('[data-focus-stop]') ? [group] : []), ...group.querySelectorAll('[data-focus-stop]')]
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
  if (el) {
    el.dataset.focused = '';
    reveal(el);
  }
}

// Bring a stop below (or above) the fold into view by smoothly scrolling
// the area it sits in, with room around it for the theme's focus effect.
// From the positions measured when the ring was worked out and the scroll
// positions the knob set (only the knob scrolls these screens): asking the
// browser where things are would force a style and layout pass, ~50 ms a
// step on the Pi.
const scrolledTo = new Map();   // scroller -> the scrollTop the knob last set

function reveal(el) {
  const g = cachedRing?.geo.get(el);
  if (!g?.scroller) return;
  const { scroller, top: elTop, bottom: elBottom, view, max } = g;
  const now = scrolledTo.get(scroller) ?? 0;
  const margin = Math.min(view * 0.12, 120);
  let top = now;
  if (elBottom + margin > now + view) top = elBottom + margin - view;
  else if (elTop - margin < now) top = elTop - margin;
  top = Math.max(0, Math.min(max, top));
  if (Math.abs(top - now) > 1) {
    scrolledTo.set(scroller, top);
    scroller.scrollTo({ top, behavior: 'smooth' });
  }
}

// Which ancestor scrolls never changes for a stop, so it's looked up once:
// getComputedStyle up the chain forces a style recalculation, and doing it
// on every knob step cost ~40 ms each on the Pi.
const scrollers = new WeakMap();

function scrollerOf(el) {
  if (!scrollers.has(el)) {
    let found = null;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const oy = getComputedStyle(p).overflowY;
      if (oy === 'auto' || oy === 'scroll') { found = p; break; }
    }
    scrollers.set(el, found);
  }
  const s = scrollers.get(el);
  return s && s.scrollHeight > s.clientHeight ? s : null;
}

// After the page changes (a new screen), keep the highlight on the same
// stop if it still exists, else go to rest.
export function refocus() {
  const { stops, rest } = ring();
  const same = currentKey && stops.find(el => el.dataset.focusStop === currentKey);
  setCurrent(same || rest);
}

// Put the highlight on a particular stop (by key), e.g. back on the button
// that opened an overlay once it closes.
export function focusKey(key) {
  const { stops, rest } = ring();
  setCurrent(stops.find(el => el.dataset.focusStop === key) || rest);
}

export function currentStopKey() {
  return currentKey;
}

export function goToRest() {
  setCurrent(ring().rest);
  // Back at rest, the screen's content scrolls back to its top.
  for (const [s, top] of scrolledTo) {
    if (!s.isConnected) { scrolledTo.delete(s); continue; }
    if (top > 0) {
      scrolledTo.set(s, 0);
      s.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }
}

// A screen being put away goes back to its top, as if built anew.
export function resetScroll(root) {
  for (const [s, top] of scrolledTo) {
    if (!s.isConnected) { scrolledTo.delete(s); continue; }
    if (top > 0 && root.contains(s)) {
      s.scrollTop = 0;
      scrolledTo.set(s, 0);
    }
  }
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
  // A short "pressed" state for the theme, like a button going down.
  const pressed = current;
  if (pressed) {
    pressed.dataset.pressed = '';
    setTimeout(() => delete pressed.dataset.pressed, 220);
  }
  pressed?.jbClick?.();
  armRestTimer();
}

// Repurpose the knob, e.g. turning changes the volume. Ends on a click or
// after MODE_TIMEOUT_MS without turning (handlers.timeoutMs for longer).
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
  modeTimer = setTimeout(exitMode, mode?.timeoutMs ?? MODE_TIMEOUT_MS);
}
