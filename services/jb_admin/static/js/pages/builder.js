// The page builder: rebuild any screen of the cabinet by dragging blocks,
// like WordPress's block editor. Edits autosave as a draft that only the
// preview shows; Publish puts them on the cabinet.
//
// #/builder/<layout>/<theme>  (theme defaults to the active one)

import { ApiError, del, get, post, put } from '../api.js';
import { button, clear, confirmDialog, debounce, h, icon, iconButton, modal, showError, toast } from '../ui.js';
import { createInspector } from '../builder/inspector.js';
import * as M from '../builder/model.js';
import { createPalette } from '../builder/palette.js';
import { createPreview, DEVICES } from '../builder/preview.js';
import { createTree } from '../builder/tree.js';

const SAVE_MS = 700;
const UNDO_LIMIT = 100;

let S = null;   // this visit's state

export async function show(main, [layoutArg, themeArg]) {
  const [schema, themesData] = await Promise.all([get('/api/blocks'), get('/api/themes')]);
  const usable = themesData.themes.filter(t => !t.problems.length);
  const theme = usable.find(t => t.id === themeArg)?.id || themesData.active;
  const layouts = await get(`/api/layouts/${encodeURIComponent(theme)}`);
  const names = layouts.layouts.map(l => l.name);
  const layout = names.includes(layoutArg) ? layoutArg : 'home';
  const data = await get(`/api/layouts/${encodeURIComponent(theme)}/${encodeURIComponent(layout)}`);

  S = {
    schema, theme, layout, themes: usable, active: themesData.active, layouts: layouts.layouts,
    themeTree: data.theme, published: data.published, hasDraft: !!data.draft,
    tree: M.clone(data.draft || data.published || data.theme || blank(layout, null)),
    selected: null, undo: [], redo: [], lastCoalesce: null, lastCoalesceAt: 0,
    saving: false, dirty: false, problems: null, tab: 'add',
  };
  build(main);
}

export function leave() {
  if (S?.dirty) S.save.flush();
  document.removeEventListener('keydown', S?.onKey);
  S?.resizeObs?.disconnect();
  document.body.classList.remove('builder-open');
  S = null;
}

function blank(layout, themeTree) {
  const root = { children: [] };
  if (themeTree?.class) root.class = themeTree.class;
  if (layout === 'frame') root.children.push({ block: 'screen' });
  return root;
}

function layoutLabel(name) {
  return S.schema.layouts[name]?.label || name.replace(/-/g, ' ').replace(/^./, c => c.toUpperCase());
}

// The page --------------------------------------------------------------------

function build(main) {
  document.body.classList.add('builder-open');
  const b = api();
  S.b = b;
  S.tree$ = createTree(b);
  S.palette = createPalette(b);
  S.inspector = createInspector(b);
  S.preview = createPreview(b);
  S.save = debounce(save, SAVE_MS);

  const status = h('span.bld-status');
  const problems = h('div.bld-problems', { hidden: true });
  const undoBtn = iconButton('undo-2', 'Undo (Ctrl+Z)', { onclick: undo });
  const redoBtn = iconButton('redo-2', 'Redo (Ctrl+Shift+Z)', { onclick: redo });
  const publishBtn = button('Publish', { variant: 'primary', onclick: publish }, 'upload');
  const modeBtn = iconButton('mouse-pointer-click', 'Click to select blocks (turn off to use the screen)', {
    'aria-pressed': 'true',
    onclick: () => {
      const selecting = modeBtn.getAttribute('aria-pressed') !== 'true';
      modeBtn.setAttribute('aria-pressed', String(selecting));
      S.preview.setInteract(!selecting);
      toast(selecting ? 'Click the preview to select blocks.' : 'The preview works like the cabinet now; click to use it.');
    },
  });
  const device = h('select.bld-device', { 'aria-label': 'Screen shape', title: 'Preview a screen of this shape', onchange: () => S.preview.setDevice(device.value) },
    Object.entries(DEVICES).map(([k, d]) => h('option', { value: k }, d.label)));
  const themeSel = h('select.bld-theme', { 'aria-label': 'Theme', onchange: () => { location.hash = `#/builder/${S.layout}/${themeSel.value}`; } },
    S.themes.map(t => h('option', { value: t.id, selected: t.id === S.theme }, t.id === S.active ? `${t.name} (active)` : t.name)));
  const layoutSel = h('select.bld-layout', { 'aria-label': 'Screen to edit', onchange: () => { location.hash = `#/builder/${layoutSel.value}/${S.theme}`; } },
    S.layouts.map(l => h('option', { value: l.name, selected: l.name === S.layout }, layoutLabel(l.name))));
  const more = iconButton('ellipsis', 'More', { onclick: () => moreMenu(more) });

  const tabs = h('div.bld-tabs', { role: 'tablist' }, [['add', 'Add', 'square-plus'], ['structure', 'Structure', 'layers'], ['inspect', 'Settings', 'settings']]
    .map(([id, label, ic]) => h('button.bld-tab', { type: 'button', role: 'tab', dataset: { tab: id }, onclick: () => setTab(id) }, icon(ic), h('span', label))));

  const root = h('div.builder',
    h('div.bld-bar',
      h('div.bld-bar-group', h('a.icon-btn.bld-exit', { href: '#/dashboard', title: 'Back to the dashboard', 'aria-label': 'Back to the dashboard' }, icon('chevron-left')),
        layoutSel, themeSel),
      h('div.bld-bar-group.bld-bar-mid', undoBtn, redoBtn, h('span.bld-sep'), device, modeBtn),
      h('div.bld-bar-group.bld-bar-end', status, more, publishBtn)),
    problems,
    // One grid, rearranged by CSS: wide screens show Settings in its own
    // column; narrower ones make it a third tab; phones put the preview on top.
    h('div.bld-body',
      tabs,
      h('div.bld-pane.bld-structure', S.tree$.el),
      h('div.bld-pane.bld-add', S.palette.el),
      h('div.bld-pane.bld-inspect', h('div.bld-pane-head', 'Settings'), S.inspector.el),
      h('div.bld-center', S.preview.el)));
  clear(main, root);
  S.root = root;
  S.ui = { status, problems, undoBtn, redoBtn, publishBtn };

  S.onKey = e => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === 'z' && !isTyping(e)) {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
    } else if (mod && e.key.toLowerCase() === 'y' && !isTyping(e)) {
      e.preventDefault();
      redo();
    }
  };
  document.addEventListener('keydown', S.onKey);

  setTab('add');
  renderAll();
  S.preview.reload();
}

const wide = () => matchMedia('(min-width: 1300px)').matches;

function isTyping(e) {
  return /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
}

function setTab(id) {
  S.tab = id;
  S.root.dataset.tab = id;
  for (const t of S.root.querySelectorAll('.bld-tab')) t.setAttribute('aria-selected', String(t.dataset.tab === id));
  if (id === 'add') S.palette.render();
}

function moreMenu(anchor) {
  const close = () => { menu.remove(); document.removeEventListener('pointerdown', outside, true); };
  const outside = e => { if (!menu.contains(e.target)) close(); };
  const item = (label, ic, fn, opts = {}) => h('button.menu-item', { type: 'button', disabled: opts.disabled, class: opts.danger ? 'danger' : undefined,
    onclick: () => { close(); fn(); } }, icon(ic), h('span', label));
  const menu = h('div.menu', { role: 'menu' },
    item('Discard draft', 'x', discardDraft, { disabled: !S.hasDraft }),
    item('Revisions…', 'rotate-ccw', showRevisions),
    item('Start from a blank page', 'file-plus', startBlank),
    item('Reset to the theme\'s layout', 'archive', resetLayout, { danger: true, disabled: !S.published && !S.hasDraft }),
    h('div.menu-sep'),
    item('Open the preview full size', 'external-link', () => open(`/preview?preview=1&theme=${encodeURIComponent(S.theme)}&screen=${S.layout === 'frame' ? 'home' : S.layout}`, '_blank')));
  const r = anchor.getBoundingClientRect();
  Object.assign(menu.style, { top: `${r.bottom + 6}px`, right: `${Math.max(8, innerWidth - r.right)}px` });
  document.body.append(menu);
  setTimeout(() => document.addEventListener('pointerdown', outside, true));
}

// Rendering -------------------------------------------------------------------

function renderAll(opts = {}) {
  S.tree$.render();
  if (!opts.quietInspector) S.inspector.render();
  if (S.tab === 'add') S.palette.render();
  S.preview.refresh();
  renderStatus();
}

function renderStatus() {
  const { status, undoBtn, redoBtn, publishBtn, problems } = S.ui;
  undoBtn.disabled = !S.undo.length;
  redoBtn.disabled = !S.redo.length;
  let text, cls;
  if (S.problems) { text = 'Can\'t save'; cls = 'bad'; }
  else if (S.saving || S.dirty) { text = 'Saving…'; cls = ''; }
  else if (S.hasDraft) { text = 'Draft saved · not on the cabinet yet'; cls = 'draft'; }
  else if (S.published) { text = 'Published · on the cabinet'; cls = 'ok'; }
  else { text = 'The theme\'s layout'; cls = ''; }
  status.textContent = text;
  status.className = `bld-status ${cls}`;
  publishBtn.disabled = !(S.hasDraft || S.dirty) || !!S.problems;
  problems.hidden = !S.problems;
  if (S.problems) clear(problems, icon('circle-alert'), h('span', `This layout can't be used yet: ${S.problems.join(' ')}`));
}

// Editing ---------------------------------------------------------------------

function api() {
  return {
    schema: S.schema,
    tree: () => S.tree,
    layout: () => S.layout,
    theme: () => S.theme,
    selected: () => S.selected,
    rootLabel: () => `${layoutLabel(S.layout)} (whole screen)`,
    surfaces: () => [S.tree$.surface, S.preview.surface],
    select(path, fromPreview) {
      S.selected = path;
      renderAll();
      S.tree$.scrollToSelected();
      S.preview.reveal(path);
      // Narrower screens show Settings as a tab: go to it.
      if (!wide()) setTab('inspect');
    },
    update(path, key, value, coalesce, quiet) {
      change(() => {
        const node = M.at(S.tree, path);
        if (value === undefined) delete node[key];
        else node[key] = value;
      }, { coalesce, quiet });
    },
    moveNode(from, parentPath, index) {
      let to;
      change(() => { to = M.move(S.tree, from, parentPath, index); });
      S.selected = to;
      renderAll();
    },
    addNode(node, parentPath, index) {
      if (!parentPath) [parentPath, index] = defaultSpot(node);
      if (!parentPath) { toast('There\'s no place for that block in this layout.', 'error'); return; }
      let to;
      change(() => { to = M.insert(S.tree, parentPath, index, node); });
      S.selected = to;
      renderAll();
      S.preview.reveal(to);
    },
    duplicate(path) {
      let to;
      change(() => { to = M.insert(S.tree, path.slice(0, -1), path[path.length - 1] + 1, M.clone(M.at(S.tree, path))); });
      S.selected = to;
      renderAll();
    },
    remove(path) {
      const label = M.describe(S.schema, M.at(S.tree, path)).label;
      change(() => M.remove(S.tree, path));
      const parent = path.slice(0, -1);
      const siblings = M.at(S.tree, parent).children.length;
      S.selected = siblings ? [...parent, Math.min(path[path.length - 1], siblings - 1)] : parent;
      renderAll();
      toast(`${label} deleted. Undo with Ctrl+Z.`);
    },
    moveBy(path, delta) {
      const i = path[path.length - 1];
      const parent = path.slice(0, -1);
      const n = M.at(S.tree, parent).children.length;
      const j = i + delta;
      if (j < 0 || j >= n) return;
      let to;
      change(() => { to = M.move(S.tree, path, parent, delta > 0 ? j + 1 : j); });
      S.selected = to;
      renderAll();
    },
    // A new container around one node: a column (the toolbar's Wrap), or a
    // row with the node still selected (Width's "Put it in a row").
    wrap(path, cls = 'jb-u-column') {
      change(() => {
        const parent = M.at(S.tree, path.slice(0, -1));
        const i = path[path.length - 1];
        parent.children[i] = { class: cls, children: [parent.children[i]] };
      });
      S.selected = cls === 'jb-u-row' ? [...path, 0] : path;
      renderAll();
    },
    unwrap(path) {
      change(() => {
        const parent = M.at(S.tree, path.slice(0, -1));
        const i = path[path.length - 1];
        parent.children.splice(i, 1, ...parent.children[i].children);
      });
      S.selected = path.slice(0, -1);
      renderAll();
    },
  };
}

// Where a clicked palette block goes: after the selected block, or at the
// end of the selected container, or at the end of the screen.
function defaultSpot(node) {
  const ok = p => M.canDrop(S.tree, S.schema, S.layout, node, null, p);
  const sel = S.selected;
  if (sel) {
    const target = M.at(S.tree, sel);
    if (M.isContainer(target) && ok(sel)) return [sel, target.children.length];
    if (sel.length && ok(sel.slice(0, -1))) return [sel.slice(0, -1), sel[sel.length - 1] + 1];
  }
  // Nothing selected: the container holding the most blocks (Home's rows,
  // the sidebar), not the outermost one, whose look the theme has fixed.
  let best = null;
  M.walk(S.tree, (n, p) => {
    if (!M.isContainer(n) || !ok(p)) return;
    const blocks = n.children.filter(c => c.block && c.block !== 'screen').length;
    if (blocks && (!best || blocks > best.blocks)) best = { path: p, blocks, size: n.children.length };
  });
  if (best) return [best.path, best.size];
  return ok([]) ? [[], S.tree.children.length] : [null, 0];
}

// Every edit goes through here: an undo step, a redraw, an autosave.
// `coalesce` merges a run of keystrokes in one field into one undo step.
function change(fn, { coalesce, quiet } = {}) {
  const before = JSON.stringify(S.tree);
  const now = Date.now();
  fn();
  if (JSON.stringify(S.tree) === before) return;
  const merge = coalesce && coalesce === S.lastCoalesce && now - S.lastCoalesceAt < 1500;
  if (!merge) {
    S.undo.push({ tree: before, selected: S.selected });
    if (S.undo.length > UNDO_LIMIT) S.undo.shift();
  }
  S.lastCoalesce = coalesce || null;
  S.lastCoalesceAt = now;
  S.redo.length = 0;
  S.dirty = true;
  S.save();
  renderAll({ quietInspector: quiet });
}

function undo() {
  const step = S.undo.pop();
  if (!step) return;
  S.redo.push({ tree: JSON.stringify(S.tree), selected: S.selected });
  restore(step);
}

function redo() {
  const step = S.redo.pop();
  if (!step) return;
  S.undo.push({ tree: JSON.stringify(S.tree), selected: S.selected });
  restore(step);
}

function restore(step) {
  S.tree = JSON.parse(step.tree);
  S.selected = step.selected && M.at(S.tree, step.selected) ? step.selected : null;
  S.lastCoalesce = null;
  S.dirty = true;
  S.save();
  renderAll();
}

// Saving and publishing ----------------------------------------------------------

async function save() {
  if (!S) return;
  const mine = S;
  const sent = JSON.stringify(mine.tree);
  mine.saving = true;
  mine.dirty = false;
  renderStatus();
  try {
    await put(`/api/layouts/${encodeURIComponent(mine.theme)}/${encodeURIComponent(mine.layout)}/draft`, { tree: JSON.parse(sent) });
    mine.hasDraft = true;
    mine.problems = null;
    if (S === mine) mine.preview.reload();
  } catch (e) {
    if (e instanceof ApiError && e.status === 400 && e.problems) mine.problems = e.problems;
    else if (S === mine) showError(e);
    if (!(e instanceof ApiError && e.status === 400)) mine.dirty = true;
  } finally {
    mine.saving = false;
    if (S === mine) renderStatus();
  }
}

async function publish() {
  if (S.dirty) await S.save.flush();
  if (S.problems) return;
  if (S.theme !== S.active && !await confirmDialog('Publish for another theme?',
    `The cabinet uses a different theme right now, so this won't show until you activate ${S.themes.find(t => t.id === S.theme)?.name}.`,
    { confirm: 'Publish' })) return;
  try {
    await post(`/api/layouts/${encodeURIComponent(S.theme)}/${encodeURIComponent(S.layout)}/publish`);
    S.hasDraft = false;
    S.published = M.clone(S.tree);
    toast(S.theme === S.active ? 'Published. The cabinet is updating.' : 'Published.', 'success');
    renderStatus();
  } catch (e) { showError(e); }
}

async function reloadLayout(message) {
  const data = await get(`/api/layouts/${encodeURIComponent(S.theme)}/${encodeURIComponent(S.layout)}`);
  S.save.cancel();
  S.dirty = false;
  S.problems = null;
  S.published = data.published;
  S.hasDraft = !!data.draft;
  S.tree = M.clone(data.draft || data.published || data.theme || blank(S.layout, null));
  S.undo = [];
  S.redo = [];
  S.selected = null;
  renderAll();
  S.preview.reload();
  if (message) toast(message, 'success');
}

async function discardDraft() {
  if (!await confirmDialog('Discard the draft?', 'Your unpublished changes to this screen go. What\'s on the cabinet stays as it is.', { confirm: 'Discard', danger: true })) return;
  try {
    S.save.cancel();
    await del(`/api/layouts/${encodeURIComponent(S.theme)}/${encodeURIComponent(S.layout)}/draft`);
    await reloadLayout('Draft discarded.');
  } catch (e) { showError(e); }
}

async function resetLayout() {
  if (!await confirmDialog('Reset to the theme\'s layout?', 'Your version of this screen, published or not, is removed and the theme\'s own layout comes back on the cabinet.', { confirm: 'Reset', danger: true })) return;
  try {
    S.save.cancel();
    await del(`/api/layouts/${encodeURIComponent(S.theme)}/${encodeURIComponent(S.layout)}`);
    await reloadLayout('Back to the theme\'s layout.');
  } catch (e) { showError(e); }
}

// Every published version, newest first. Restoring loads it into the editor
// as a draft (undo still works), so nothing changes on the cabinet until
// Publish.
async function showRevisions() {
  let list;
  try {
    list = (await get(`/api/layouts/${encodeURIComponent(S.theme)}/${encodeURIComponent(S.layout)}/revisions`)).revisions;
  } catch (e) { showError(e); return; }
  const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  const restore = async rev => {
    try {
      const { tree } = await get(`/api/layouts/${encodeURIComponent(S.theme)}/${encodeURIComponent(S.layout)}/revisions/${rev.id}`);
      m.close();
      change(() => { S.tree = tree; });
      S.selected = null;
      renderAll();
      toast(`Restored the version from ${fmt.format(rev.at * 1000)} as a draft. Publish to put it on the cabinet.`, 'success', 6000);
    } catch (e) { showError(e); }
  };
  const body = list.length
    ? h('ul.list.revisions', list.map((rev, i) => h('li',
      icon(i === 0 ? 'circle-check' : 'circle-dot'),
      h('span', { style: { flex: 1 } }, fmt.format(rev.at * 1000), i === 0 ? h('span.badge.ok', { style: { marginLeft: '8px' } }, 'Latest') : null),
      button('Restore', { variant: 'small', onclick: () => restore(rev) }, 'rotate-ccw'))))
    : h('p.muted', 'Nothing published yet. Each time you publish, the version is kept here (the last 20).');
  const m = modal({ title: `Revisions: ${layoutLabel(S.layout)}`, body, actions: [{ label: 'Close' }] });
}

function startBlank() {
  change(() => { S.tree = blank(S.layout, S.themeTree); });
  S.selected = [];
  renderAll();
  toast('A blank page. Add blocks from the Add panel; undo brings the old one back.');
}
