// Popups open over everything (docs/theme-format.md, "Popups"). One at a
// time: opening another replaces it. A popup can hold knob stops (template
// elements marked data-stop="name"); while it does, the knob stays inside
// it until it closes.

import { bind, fromTemplate, slot } from './bind.js';
import { makeStop } from './focus.js';

let templates = {};
let layer = null;
let current = null;  // {name, frame, onClose}

export function initPopups(blockTemplates) {
  templates = blockTemplates;
  layer = document.createElement('div');
  layer.id = 'jb-popup-layer';
  layer.hidden = true;
  document.body.append(layer);
}

// opts.stops: {stopName: handler} for the template's data-stop elements.
// opts.onClose: called when the popup closes (by any means).
export function openPopup(name, data, opts = {}) {
  if (current) closePopup();
  const frame = document.createElement('div');
  frame.className = 'jb-popup-frame';
  frame.dataset.popup = name;
  frame.append(fromTemplate(templates.popup));
  slot(frame, 'content')?.append(fromTemplate(templates[`popup-${name}`]));
  if (opts.stops) {
    frame.dataset.modal = '';
    for (const el of frame.querySelectorAll('[data-stop]')) {
      const handler = opts.stops[el.dataset.stop];
      if (handler) makeStop(el, `popup:${name}:${el.dataset.stop}`, handler);
    }
  }
  layer.replaceChildren(frame);
  layer.hidden = false;
  document.documentElement.dataset.popup = name;
  current = { name, frame, onClose: opts.onClose };
  updatePopup(name, data);
}

// Re-bind the open popup, if it's still `name`.
export function updatePopup(name, data, vars = {}) {
  if (current?.name !== name) return;
  bind(current.frame, data);
  const content = slot(current.frame, 'content');
  if (content) bind(content, data);
  for (const [k, v] of Object.entries(vars)) current.frame.style.setProperty(k, v);
}

export function closePopup(name) {
  if (!current || (name && current.name !== name)) return;
  const closed = current;
  current = null;
  layer.hidden = true;
  layer.replaceChildren();
  delete document.documentElement.dataset.popup;
  closed.onClose?.();
}

export function popupOpen() {
  return current?.name || null;
}
