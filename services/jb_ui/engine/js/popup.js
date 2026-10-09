// Popups open over everything (docs/theme-format.md, "Popups"). One at a
// time: opening another replaces it.

import { bind, fromTemplate, slot } from './bind.js';

let templates = {};
let layer = null;
let current = null;  // {name, frame}

export function initPopups(blockTemplates) {
  templates = blockTemplates;
  layer = document.createElement('div');
  layer.id = 'jb-popup-layer';
  layer.hidden = true;
  document.body.append(layer);
}

export function openPopup(name, data) {
  const frame = document.createElement('div');
  frame.className = 'jb-popup-frame';
  frame.dataset.popup = name;
  frame.append(fromTemplate(templates.popup));
  slot(frame, 'content')?.append(fromTemplate(templates[`popup-${name}`]));
  layer.replaceChildren(frame);
  layer.hidden = false;
  document.documentElement.dataset.popup = name;
  current = { name, frame };
  updatePopup(name, data);
}

// Re-bind the open popup, if it's still `name`.
export function updatePopup(name, data) {
  if (current?.name !== name) return;
  bind(current.frame, data);
  const content = slot(current.frame, 'content');
  if (content) bind(content, data);
}

export function closePopup(name) {
  if (!current || (name && current.name !== name)) return;
  layer.hidden = true;
  layer.replaceChildren();
  delete document.documentElement.dataset.popup;
  current = null;
}

export function popupOpen() {
  return current?.name || null;
}
