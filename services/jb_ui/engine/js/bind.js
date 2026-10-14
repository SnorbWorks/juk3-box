// Block markup: turning a template into elements, and filling those
// elements with data (docs/theme-format.md, "Block markup").
//
// Binding is repeatable: blocks build their markup once and re-bind it on
// every change, so nothing flickers and a theme's elements keep their
// state (e.g. CSS transitions).

import { watchArt } from './art.js';

// Each template is parsed once; every use is a copy (the song board makes
// a hundred of the same one).
const parsed = new Map();

export function fromTemplate(html) {
  let t = parsed.get(html || '');
  if (!t) {
    t = document.createElement('template');
    t.innerHTML = html || '';
    parsed.set(html || '', t);
  }
  return t.content.cloneNode(true);
}

export function bind(root, data) {
  for (const el of own(root, '[data-text]')) {
    const v = data[el.dataset.text];
    const text = v === undefined || v === null ? '' : String(v);
    if (el.textContent !== text) el.textContent = text;
  }
  for (const el of own(root, '[data-src]')) {
    const v = data[el.dataset.src];
    if (v) {
      if (el.getAttribute('src') !== v) {
        // Readable pixels (for art colors) need CORS; set before the src.
        el.crossOrigin = 'anonymous';
        el.setAttribute('src', v);
      }
      watchArt(el);
    } else {
      el.removeAttribute('src');
    }
  }
  for (const el of own(root, '[data-icon]')) setIcon(el, data[el.dataset.icon]);
  // Only when it changes: writing the same value still makes the browser
  // redo styles, and Now Playing re-binds twice a second.
  for (const el of own(root, '[data-if]')) setHidden(el, !data[el.dataset.if]);
  for (const el of own(root, '[data-if-not]')) setHidden(el, !!data[el.dataset.ifNot]);
}

function setHidden(el, hidden) {
  if (el.hidden !== hidden) el.hidden = hidden;
}

// An icon from the engine's icon list: library SVG (already cleaned by
// jb-ui), a theme's own image file, or one the engine draws itself.
export function setIcon(el, icon) {
  const key = icon ? JSON.stringify(icon) : '';
  if (el.jbIcon === key) return;
  el.jbIcon = key;
  if (!icon) {
    el.replaceChildren();
  } else if (icon.svg) {
    el.innerHTML = icon.svg;
  } else if (icon.url) {
    const img = document.createElement('img');
    img.src = icon.url;
    img.alt = '';
    el.replaceChildren(img);
  } else if (icon.builtin === 'eq') {
    // Equalizer bars; the theme animates them while html[data-playing].
    const eq = document.createElement('span');
    eq.className = 'jb-eq';
    for (let i = 0; i < 4; i++) eq.append(document.createElement('i'));
    el.replaceChildren(eq);
  }
}

export function slot(root, name) {
  return root.querySelector(`[data-slot="${name}"]`);
}

// Elements under root that belong to root's own template, not to cards
// placed in one of its slots (those are bound with their own data).
function own(root, selector) {
  return [...root.querySelectorAll(selector)].filter(el => {
    const s = el.closest('[data-slot]');
    return !s || s === root || !root.contains(s);
  });
}
