// Appearance: a theme's global options -- colors, fonts, shape, background
// -- like WordPress's Customizer. Pick a style (a set of colors that go
// together), then change single options on top of it. The preview shows
// every change at once; changes autosave as a draft; Publish puts them on
// the cabinet.
//
// #/appearance/<theme>  (theme defaults to the active one)

import { del, get, post, put } from '../api.js';
import { button, clear, confirmDialog, debounce, field, h, icon, iconButton, modal, showError, switchControl, toast } from '../ui.js';
import { createPreview, DEVICES } from '../builder/preview.js';
import { pickPicture } from './media.js';

const SAVE_MS = 600;
const GROUP_LABELS = { colors: 'Colors', fonts: 'Fonts', shape: 'Shape', background: 'Background', motion: 'Motion' };
const SCREENS = { home: 'Home', 'all-songs': 'All Songs' };
// The colors a style card shows, when the theme has them.
const SWATCHES = ['panel', 'button', 'accent', 'highlight', 'text'];

let S = null;   // this visit's state

export async function show(main, [themeArg]) {
  const themesData = await get('/api/themes');
  const usable = themesData.themes.filter(t => !t.problems.length);
  const theme = usable.find(t => t.id === themeArg)?.id || themesData.active;
  const data = await get(`/api/customize/${encodeURIComponent(theme)}`);
  const board = await get('/api/board').catch(() => null);   // per-board suggestions
  const saved = data.draft || data.published || { style: null, values: {} };
  S = {
    theme, themes: usable, active: themesData.active, name: data.name,
    options: data.options, styles: data.styles, groups: data.groups, fonts: data.fonts,
    style: saved.style || data.styles[0]?.id || null, values: { ...(saved.values || {}) },
    published: data.published, hasDraft: !!data.draft, dirty: false, saving: false, screen: 'home',
    board,
  };
  build(main);
}

export function leave() {
  if (S?.dirty) S.save.flush();
  document.body.classList.remove('builder-open');
  S = null;
}

// What the cabinet would show: the theme's defaults, the style's, then mine.
function effective() {
  const out = {};
  for (const o of S.options) out[o.id] = o.default;
  Object.assign(out, S.styles.find(s => s.id === S.style)?.values || {});
  return Object.assign(out, S.values);
}

function styleValue(o) {
  const st = S.styles.find(s => s.id === S.style)?.values || {};
  return o.id in st ? st[o.id] : o.default;
}

// The page --------------------------------------------------------------------

function build(main) {
  document.body.classList.add('builder-open');
  S.save = debounce(save, SAVE_MS);
  S.preview = createPreview({
    tree: () => ({ children: [] }), layout: () => S.screen, theme: () => S.theme, schema: { blocks: {} },
    selected: () => null, select() {}, rootLabel: () => '',
  });
  // A plain view of the screen: no selecting blocks here.
  S.preview.setInteract(true);

  const status = h('span.bld-status');
  const publishBtn = button('Publish', { variant: 'primary', onclick: publish }, 'upload');
  const themeSel = h('select.bld-theme', { 'aria-label': 'Theme', onchange: () => { location.hash = `#/appearance/${themeSel.value}`; } },
    S.themes.map(t => h('option', { value: t.id, selected: t.id === S.theme }, t.id === S.active ? `${t.name} (active)` : t.name)));
  const screenSel = h('select', { 'aria-label': 'Screen to preview', onchange: () => { S.screen = screenSel.value; S.preview.reload(); } },
    Object.entries(SCREENS).map(([k, label]) => h('option', { value: k }, label)));
  const device = h('select.bld-device', { 'aria-label': 'Screen shape', onchange: () => S.preview.setDevice(device.value) },
    Object.entries(DEVICES).map(([k, d]) => h('option', { value: k }, d.label)));
  const more = iconButton('ellipsis', 'More', { onclick: () => moreMenu(more) });
  const panel = h('div.cz-panel');

  const root = h('div.builder.customize',
    h('div.bld-bar',
      h('div.bld-bar-group', h('a.icon-btn.bld-exit', { href: '#/themes', title: 'Back to Themes', 'aria-label': 'Back to Themes' }, icon('chevron-left')),
        h('span.bld-layout', 'Appearance'), themeSel),
      h('div.bld-bar-group.bld-bar-mid', screenSel, device),
      h('div.bld-bar-group.bld-bar-end', status, more, publishBtn)),
    h('div.cz-body', h('div.cz-side', panel), h('div.bld-center', S.preview.el)));
  clear(main, root);
  S.ui = { status, publishBtn, panel };
  render();
  S.preview.reload();
}

function render() {
  const { panel } = S.ui;
  const custom = Object.keys(S.values).length;
  const byGroup = {};
  for (const o of S.options) (byGroup[o.group || 'colors'] ||= []).push(o);
  clear(panel,
    S.options.length ? null : h('div.notice', icon('info'), h('span', `${S.name} has no options to change.`)),
    S.styles.length ? h('section.cz-section',
      h('div.cz-head', h('h2', 'Style'),
        custom ? button('Undo my changes', { variant: 'small ghost', onclick: () => { S.values = {}; changed(); } }, 'rotate-ccw') : null),
      h('p.muted.cz-lead', custom
        ? `Based on ${S.styles.find(s => s.id === S.style)?.label}, with ${custom} change${custom > 1 ? 's' : ''} of yours.`
        : 'Colors that go together. Pick one, then change anything below.'),
      h('div.cz-styles', S.styles.map(styleCard))) : null,
    S.groups.filter(g => byGroup[g]).map(g => h('section.cz-section',
      h('div.cz-head', h('h2', GROUP_LABELS[g] || g)),
      byGroup[g].map(control))));
  renderStatus();
}

function styleCard(s) {
  const vals = { ...Object.fromEntries(S.options.map(o => [o.id, o.default])), ...s.values };
  const on = s.id === S.style;
  return h('button.cz-style', {
    type: 'button', 'aria-pressed': String(on),
    onclick: async () => {
      if (s.id === S.style) return;
      if (Object.keys(S.values).length && !await confirmDialog(`Switch to ${s.label}?`,
        'Your own changes go, and every option takes the new style\'s value. Undo with the style you had.', { confirm: 'Switch' })) return;
      S.style = s.id;
      S.values = {};
      changed();
    },
  },
  h('span.cz-swatches', SWATCHES.filter(k => vals[k]).map(k => h('span.cz-swatch', { style: { background: vals[k] } }))),
  h('span.cz-style-name', s.label, on ? icon('check') : null),
  s.description ? h('span.cz-style-desc', s.description) : null);
}

function control(o) {
  const value = effective()[o.id];
  const mine = o.id in S.values;
  const set = v => {
    if (JSON.stringify(v) === JSON.stringify(styleValue(o))) delete S.values[o.id];
    else S.values[o.id] = v;
    changed(true);
  };
  const reset = mine ? iconButton('rotate-ccw', 'Back to the style\'s value', { class: 'cz-reset', onclick: () => { delete S.values[o.id]; changed(); } }) : null;
  let input;
  switch (o.type) {
    case 'color': {
      const text = h('input.cz-hex', { type: 'text', value, spellcheck: false, maxLength: 7, 'aria-label': `${o.label} (hex)`,
        onchange: () => { const v = text.value.trim().toLowerCase(); if (/^#[0-9a-f]{6}$/.test(v)) { pick.value = v; set(v); render(); } else text.value = pick.value; } });
      // Live while picking; the panel redraws (counts, reset buttons) once done.
      const pick = h('input.cz-color', { type: 'color', value, 'aria-label': o.label,
        oninput: () => { text.value = pick.value; set(pick.value); }, onchange: () => render() });
      input = h('div.cz-color-row', pick, text, reset);
      break;
    }
    case 'range': {
      const out = h('span.cz-range-value', fmtRange(o, value));
      const r = h('input', { type: 'range', min: o.min, max: o.max, step: o.step, value, 'aria-label': o.label,
        oninput: () => { out.textContent = fmtRange(o, Number(r.value)); set(Number(r.value)); }, onchange: () => render() });
      input = h('div.cz-range-row', r, out, reset);
      break;
    }
    case 'toggle':
      return h('div.field.cz-toggle', h('div.cz-toggle-row', switchControl(!!value, v => { set(v); render(); }, o.label), reset),
        o.help ? h('span.field-help', o.help) : null);
    case 'font': {
      const sel = h('select', { 'aria-label': o.label, onchange: () => { set(sel.value); render(); } },
        h('option', { value: 'theme', selected: value === 'theme' }, `The theme's font${o.family ? ` (${o.family.split(',')[0].replace(/"/g, '')})` : ''}`),
        S.fonts.map(f => h('option', { value: f.id, selected: value === f.id }, `${f.label}${f.style ? ` · ${f.style}` : ''}`)));
      input = h('div.cz-color-row', sel, reset);
      break;
    }
    case 'picture': {
      const url = !value ? null : value.startsWith('/media/') ? value : `/themes/${S.theme}/${value}`;
      const isVideo = /\.(mp4|m4v|mov|webm|mkv)$/i.test(String(value));
      input = h('div.picture-field',
        h('div.picture-thumb', !url ? icon('image') : isVideo ? h('video', { src: `${url}#t=1`, muted: true, preload: 'metadata' }) : h('img', { src: url, alt: '' })),
        h('div.picture-buttons',
          button(value ? 'Change' : 'Choose', { variant: 'small', onclick: async () => { const got = await pickPicture(S.theme, value, { video: !!o.video }); if (got) { set(got); render(); } } }, 'image'),
          value ? button('Use the theme\'s', { variant: 'small ghost', onclick: () => { set(''); render(); } }) : null));
      const videoNote = S.board?.features?.background_video;
      if (isVideo && videoNote) {
        input = h('div.stack', input, h('div.notice.warn', icon('circle-alert'),
          h('span', `Not suggested on a ${S.board.name || 'board like this'}: ${videoNote.why}`)));
      }
      break;
    }
    default:
      return null;
  }
  return field(o.label, input, o.help);
}

function fmtRange(o, v) {
  // Ranges around 1 read as percentages ("120%"); others as numbers.
  return o.max <= 2 ? `${Math.round(v * 100)}%` : String(v);
}

// Saving -----------------------------------------------------------------------

function changed(keepPanel) {
  S.dirty = true;
  postToPreview();
  S.save();
  if (keepPanel) renderStatus(); else render();
}

// Live: the preview applies the values at once; the draft saves after.
function postToPreview() {
  const values = effective();
  for (const f of S.preview.el.querySelectorAll('iframe')) {
    f.contentWindow?.postMessage({ type: 'jb-customize', values }, location.origin);
  }
}

async function save() {
  if (!S) return;
  S.saving = true;
  S.dirty = false;
  renderStatus();
  try {
    const res = await put(`/api/customize/${encodeURIComponent(S.theme)}/draft`, { style: S.style, values: S.values });
    S.hasDraft = true;
    S.values = { ...res.saved.values };
  } catch (e) { showError(e); }
  S.saving = false;
  if (S) renderStatus();
}

function renderStatus() {
  const { status, publishBtn } = S.ui;
  let text, cls;
  if (S.saving || S.dirty) { text = 'Saving…'; cls = ''; }
  else if (S.hasDraft) { text = 'Draft saved · not on the cabinet yet'; cls = 'draft'; }
  else if (S.published) { text = 'Published · on the cabinet'; cls = 'ok'; }
  else { text = 'The theme\'s defaults'; cls = ''; }
  status.textContent = text;
  status.className = `bld-status ${cls}`;
  publishBtn.disabled = !(S.hasDraft || S.dirty);
}

async function publish() {
  if (S.dirty) await S.save.flush();
  if (S.theme !== S.active && !await confirmDialog('Publish for another theme?',
    `The cabinet uses a different theme right now, so this won't show until you activate ${S.name}.`, { confirm: 'Publish' })) return;
  try {
    await post(`/api/customize/${encodeURIComponent(S.theme)}/publish`);
    S.hasDraft = false;
    S.published = { style: S.style, values: { ...S.values } };
    toast(S.theme === S.active ? 'Published. The cabinet is updating.' : 'Published.', 'success');
    renderStatus();
  } catch (e) { showError(e); }
}

async function reloadFromServer(message) {
  S.save.cancel();
  const data = await get(`/api/customize/${encodeURIComponent(S.theme)}`);
  const saved = data.draft || data.published || { style: null, values: {} };
  Object.assign(S, { style: saved.style || S.styles[0]?.id || null, values: { ...(saved.values || {}) },
    published: data.published, hasDraft: !!data.draft, dirty: false });
  render();
  S.preview.reload();
  if (message) toast(message, 'success');
}

function moreMenu(anchor) {
  const close = () => { menu.remove(); document.removeEventListener('pointerdown', outside, true); };
  const outside = e => { if (!menu.contains(e.target)) close(); };
  const item = (label, ic, fn, opts = {}) => h('button.menu-item', { type: 'button', disabled: opts.disabled, class: opts.danger ? 'danger' : undefined,
    onclick: () => { close(); fn(); } }, icon(ic), h('span', label));
  const menu = h('div.menu', { role: 'menu' },
    item('Discard draft', 'x', discardDraft, { disabled: !S.hasDraft }),
    item('Revisions…', 'rotate-ccw', showRevisions),
    item('Reset to the theme\'s defaults', 'archive', resetAll, { danger: true, disabled: !S.published && !S.hasDraft }),
    h('div.menu-sep'),
    item('Edit this theme\'s layouts', 'layout-template', () => { location.hash = `#/builder/home/${S.theme}`; }));
  const r = anchor.getBoundingClientRect();
  Object.assign(menu.style, { top: `${r.bottom + 6}px`, right: `${Math.max(8, innerWidth - r.right)}px` });
  document.body.append(menu);
  setTimeout(() => document.addEventListener('pointerdown', outside, true));
}

async function discardDraft() {
  if (!await confirmDialog('Discard the draft?', 'Your unpublished changes go. What\'s on the cabinet stays as it is.', { confirm: 'Discard', danger: true })) return;
  try {
    S.save.cancel();
    await del(`/api/customize/${encodeURIComponent(S.theme)}/draft`);
    await reloadFromServer('Draft discarded.');
  } catch (e) { showError(e); }
}

async function resetAll() {
  if (!await confirmDialog('Reset to the theme\'s defaults?', 'Your style and changes, published or not, are removed and the theme\'s own look comes back on the cabinet.', { confirm: 'Reset', danger: true })) return;
  try {
    S.save.cancel();
    await del(`/api/customize/${encodeURIComponent(S.theme)}`);
    await reloadFromServer('Back to the theme\'s defaults.');
  } catch (e) { showError(e); }
}

async function showRevisions() {
  let list;
  try {
    list = (await get(`/api/customize/${encodeURIComponent(S.theme)}/revisions`)).revisions;
  } catch (e) { showError(e); return; }
  const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  const restore = async rev => {
    try {
      const { values } = await get(`/api/customize/${encodeURIComponent(S.theme)}/revisions/${rev.id}`);
      m.close();
      S.style = values.style || S.styles[0]?.id || null;
      S.values = { ...(values.values || {}) };
      changed();
      toast(`Restored the version from ${fmt.format(rev.at * 1000)} as a draft. Publish to put it on the cabinet.`, 'success', 6000);
    } catch (e) { showError(e); }
  };
  const body = list.length
    ? h('ul.list.revisions', list.map((rev, i) => h('li',
      icon(i === 0 ? 'circle-check' : 'circle-dot'),
      h('span', { style: { flex: 1 } }, fmt.format(rev.at * 1000), i === 0 ? h('span.badge.ok', { style: { marginLeft: '8px' } }, 'Latest') : null),
      button('Restore', { variant: 'small', onclick: () => restore(rev) }, 'rotate-ccw'))))
    : h('p.muted', 'Nothing published yet. Each time you publish, the version is kept here (the last 20).');
  const m = modal({ title: `Revisions: ${S.name} appearance`, body, actions: [{ label: 'Close' }] });
}
