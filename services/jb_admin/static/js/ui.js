// Small DOM helpers shared by every page: element building, icons,
// toasts and dialogs. No framework: the admin is plain modules.

import { get } from './api.js';

// h('div.card#main', {onclick}, children...) -> element.
export function h(spec, props, ...children) {
  if (props instanceof Node || typeof props === 'string' || Array.isArray(props)) {
    children.unshift(props);
    props = null;
  }
  const [, tag = 'div', rest = ''] = spec.match(/^([a-z0-9-]*)(.*)$/i);
  const el = document.createElement(tag || 'div');
  for (const part of rest.match(/[.#][^.#]+/g) || []) {
    if (part[0] === '.') el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') el.className += ` ${v}`;
    else if (k === 'style' && typeof v === 'object') {
      // Custom properties (--depth) only take through setProperty.
      for (const [p, val] of Object.entries(v)) {
        if (p.startsWith('--')) el.style.setProperty(p, String(val));
        else el.style[p] = val;
      }
    }
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// Icons: the bundled Lucide set, fetched once at start.
const ICON_NAMES = [
  'house', 'keyboard', 'layout-template', 'palette', 'image', 'settings', 'boxes', 'user', 'log-out', 'plus',
  'trash-2', 'copy', 'chevron-up', 'chevron-down', 'chevron-right', 'chevron-left', 'grip-vertical',
  'eye', 'undo-2', 'redo-2', 'monitor', 'smartphone', 'check', 'x', 'upload', 'heading', 'type',
  'rows-3', 'list-music', 'disc-3', 'layout-grid', 'info', 'menu', 'navigation', 'panel-left',
  'square-dashed', 'play', 'pause', 'music', 'bluetooth', 'volume-2', 'circle-alert', 'rotate-ccw',
  'ellipsis', 'layers', 'mouse-pointer-click', 'square-plus', 'tablet', 'external-link', 'refresh-cw',
  'box', 'activity', 'key-round', 'space', 'app-window', 'group', 'pencil', 'library', 'cpu', 'radio',
  'list-ordered', 'grid-2x2', 'move-vertical', 'scan', 'lock', 'sparkles', 'columns-3', 'rows-2',
  'arrow-up-down', 'frame', 'file-plus', 'circle-check', 'circle-x', 'circle-dot', 'shuffle',
  'square-mouse-pointer', 'hand', 'maximize-2', 'monitor-smartphone', 'link', 'gauge', 'archive',
  'arrow-up-left', 'swatch-book', 'puzzle', 'square', 'power', 'scroll-text', 'shield-check', 'cpu', 'map-pin', 'search', 'usb', 'eject', 'hard-drive', 'terminal', 'cable',
  'wand-sparkles', 'download', 'package', 'triangle-alert',
];
const icons = {};

export async function loadIcons() {
  try {
    Object.assign(icons, await get(`/api/icons?names=${ICON_NAMES.join(',')}`));
  } catch { /* buttons still have their labels */ }
}

export function icon(name, cls = '') {
  const span = h(`span.icon${cls ? '.' + cls : ''}`, { 'aria-hidden': 'true' });
  // Our own bundled icon set, cleaned by the server.
  if (icons[name]) span.innerHTML = icons[name];
  return span;
}

export function button(label, props = {}, iconName) {
  const { variant = '', ...rest } = props;
  return h(`button.btn${variant ? '.' + variant.split(' ').join('.') : ''}`, { type: 'button', ...rest },
    iconName ? icon(iconName) : null, label ? h('span.btn-label', label) : null);
}

export function iconButton(iconName, title, props = {}) {
  return h('button.icon-btn', { type: 'button', title, 'aria-label': title, ...props }, icon(iconName));
}

// Toasts: short messages at the bottom.
export function toast(message, kind = 'info', ms = 3500) {
  const t = h(`div.toast.${kind}`, { role: kind === 'error' ? 'alert' : 'status' },
    icon(kind === 'error' ? 'circle-alert' : kind === 'success' ? 'circle-check' : 'info'), h('span', message));
  document.getElementById('toasts').append(t);
  requestAnimationFrame(() => t.classList.add('in'));
  setTimeout(() => { t.classList.remove('in'); setTimeout(() => t.remove(), 300); }, ms);
}

export function showError(e) {
  const msg = e?.problems ? `${e.message} ${formatProblems(e.problems)}` : (e?.message || String(e));
  toast(msg, 'error', 6000);
}

export function formatProblems(problems) {
  if (Array.isArray(problems)) return problems.join(' ');
  if (problems && typeof problems === 'object') return Object.values(problems).join(' ');
  return '';
}

// Dialogs: never the browser's own (they block everything).
export function modal({ title, body, actions = [], wide = false, onClose }) {
  const close = () => { layer.remove(); document.removeEventListener('keydown', onKey); onClose?.(); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  const box = h(`div.modal${wide ? '.wide' : ''}`, { role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div.modal-head', h('h2', title), iconButton('x', 'Close', { onclick: close })),
    h('div.modal-body', body),
    actions.length ? h('div.modal-actions', actions.map(a => a instanceof Node ? a : button(a.label, {
      variant: a.variant, onclick: async () => { if ((await a.onclick?.()) !== false) close(); },
    }, a.icon))) : null);
  const layer = h('div.modal-layer', { onclick: e => { if (e.target === layer) close(); } }, box);
  document.body.append(layer);
  document.addEventListener('keydown', onKey);
  box.querySelector('input, select, textarea, button.primary')?.focus();
  return { close, box };
}

export function confirmDialog(title, message, { confirm = 'OK', danger = false } = {}) {
  return new Promise(resolve => {
    let answered = false;
    modal({
      title,
      body: h('p', message),
      onClose: () => { if (!answered) resolve(false); },
      actions: [
        { label: 'Cancel', onclick: () => { answered = true; resolve(false); } },
        { label: confirm, variant: danger ? 'danger' : 'primary', onclick: () => { answered = true; resolve(true); } },
      ],
    });
  });
}

let fieldIds = 0;

// A labelled control. Only a single input, select or textarea is tied to
// the label: wrapping a list of checkboxes in a <label> would make a click
// anywhere in it toggle the first one.
export function field(label, control, help) {
  const single = control instanceof HTMLElement && /^(INPUT|SELECT|TEXTAREA)$/.test(control.tagName);
  if (single && !control.id) control.id = `f${++fieldIds}`;
  const name = single ? h('label.field-label', { for: control.id }, label) : h('span.field-label', label);
  return h('div.field', name, control, help ? h('span.field-help', help) : null);
}

// The one account's name, "admin": shown beside a password so a phone's
// password manager has a name to file it under (iOS won't save without one).
export function userField() {
  return field('User name', h('input', { type: 'text', name: 'username', autocomplete: 'username', value: 'admin', readOnly: true }));
}

export function switchControl(checked, onchange, label) {
  const input = h('input', { type: 'checkbox', checked, role: 'switch', onchange: () => onchange(input.checked) });
  // A label, so a click on the track or the words flips the (hidden) box.
  return h('label.switch', input, h('span.switch-track', h('span.switch-thumb')), label ? h('span', label) : null);
}

export function pageHeader(title, subtitle, ...actions) {
  return h('header.page-head',
    h('div', h('h1', title), subtitle ? h('p.page-sub', subtitle) : null),
    actions.length ? h('div.page-actions', actions) : null);
}

export function emptyState(iconName, title, text, action) {
  return h('div.empty', icon(iconName, 'big'), h('h3', title), text ? h('p', text) : null, action || null);
}

export function debounce(fn, ms) {
  let t;
  const d = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  d.flush = (...args) => { clearTimeout(t); return fn(...args); };
  d.cancel = () => clearTimeout(t);
  return d;
}

export function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
