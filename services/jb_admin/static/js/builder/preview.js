// The live preview: the cabinet's real engine in a frame, in preview mode
// (drafts shown, nothing sent to the jukebox), scaled to fit. The engine
// tags every element with its place in the layout (data-jb-path), so a
// click selects that block, hovering outlines it, and blocks can be
// dropped straight onto it.

import { h, icon, iconButton } from '../ui.js';
import { at, canDrop, canDelete, canDuplicate, describe, isContainer } from './model.js';

export const DEVICES = {
  '16:9': { w: 1920, h: 1080, label: '16:9' },
  '16:10': { w: 1920, h: 1200, label: '16:10' },
  '4:3': { w: 1600, h: 1200, label: '4:3' },
};

export function createPreview(b) {
  // b: tree(), schema, layout(), theme(), selected(), select(path),
  // moveBy, duplicate, remove, rootLabel()
  let device = DEVICES['16:9'];
  let scale = 1;
  let frame = null;          // the iframe showing
  let loading = null;        // the one loading, swapped in when ready
  let interact = false;      // clicks go to the engine instead of selecting
  let hoverPath = null;

  const frameBox = h('div.preview-frame');
  const hoverBox = h('div.pv-box.pv-hover', { hidden: true }, h('span.pv-tag'));
  const tools = h('div.pv-tools');
  const selectBox = h('div.pv-box.pv-select', { hidden: true }, tools);
  const dropLine = h('div.pv-drop', { hidden: true });
  const overlay = h('div.preview-overlay', hoverBox, selectBox, dropLine);
  const status = h('div.preview-status', { hidden: true }, h('span.spinner'), 'Updating preview…');
  const stage = h('div.preview-stage', h('div.preview-fit', frameBox, overlay), status);

  new ResizeObserver(() => fit()).observe(stage);

  function fit() {
    const pad = 16;
    const sw = Math.max(100, stage.clientWidth - pad * 2);
    const sh = Math.max(100, stage.clientHeight - pad * 2);
    scale = Math.min(sw / device.w, sh / device.h);
    const w = Math.floor(device.w * scale);
    const hgt = Math.floor(device.h * scale);
    for (const el of [frameBox, overlay]) Object.assign(el.style, { width: `${w}px`, height: `${hgt}px` });
    for (const f of frameBox.querySelectorAll('iframe')) {
      Object.assign(f.style, { width: `${device.w}px`, height: `${device.h}px`, transform: `scale(${scale})` });
    }
    refresh();
  }

  function url() {
    const screen = b.layout() === 'frame' ? 'home' : b.layout();
    return `/preview?preview=1&theme=${encodeURIComponent(b.theme())}&screen=${encodeURIComponent(screen)}`;
  }

  // Load the preview again (after a save). The new frame loads behind the
  // old one and replaces it when ready, so the preview never flashes.
  function reload() {
    loading?.remove();
    const f = h('iframe.loading', { src: url(), title: 'Preview of the cabinet screen', tabIndex: -1 });
    Object.assign(f.style, { width: `${device.w}px`, height: `${device.h}px`, transform: `scale(${scale})` });
    loading = f;
    status.hidden = false;
    f.addEventListener('load', () => {
      if (loading !== f) return;
      // Give the engine a moment to build the screen from the bus data.
      const ready = () => {
        if (loading !== f) return;
        frame?.remove();
        frame = f;
        loading = null;
        f.classList.remove('loading');
        status.hidden = true;
        wire(f);
        // Keep the selected block in view (a new one may be below the fold).
        const sel = b.selected();
        if (sel?.length) elementFor(sel)?.scrollIntoView({ block: 'nearest' });
        refresh();
      };
      waitForEngine(f).then(ready);
    });
    frameBox.append(f);
  }

  function waitForEngine(f) {
    return new Promise(resolve => {
      const t0 = Date.now();
      const check = () => {
        const doc = f.contentDocument;
        if (doc?.querySelector('[data-jb-path]') || doc?.getElementById('jb-error') || Date.now() - t0 > 6000) {
          setTimeout(resolve, 150);
        } else {
          setTimeout(check, 60);
        }
      };
      check();
    });
  }

  function wire(f) {
    const doc = f.contentDocument;
    if (!doc) return;
    // The kiosk hides the mouse pointer; the preview needs it. An empty
    // row or column has no size of its own, so here (never on the cabinet)
    // it gets a box to drop blocks into until something is in it.
    const style = doc.createElement('style');
    style.textContent = `html, body, * { cursor: default !important; }
      .jb-container:empty {
        display: flex !important; align-items: center; justify-content: center;
        min-height: 9vh; min-width: 12vh; box-sizing: border-box; border-radius: 1vh;
        outline: 2px dashed rgba(140, 170, 255, 0.7); outline-offset: -2px;
        background: rgba(120, 150, 255, 0.1);
      }
      .jb-container:empty::after {
        content: "Drop blocks here"; font: 600 1.7vh system-ui, sans-serif;
        color: rgba(205, 218, 255, 0.9);
      }`;
    doc.head.append(style);
    doc.addEventListener('pointermove', e => {
      if (interact) return;
      const hit = elementAt(doc, e.target);
      hoverPath = hit ? hit.path : null;
      refresh();
    });
    doc.addEventListener('pointerleave', () => { hoverPath = null; refresh(); });
    doc.addEventListener('click', e => {
      if (interact) return;
      e.preventDefault();
      e.stopPropagation();
      const hit = elementAt(doc, e.target);
      if (hit) b.select(hit.path);
    }, true);
    doc.addEventListener('scroll', () => refresh(), true);
    // Scrolling the preview with a wheel over the overlay boxes.
    f.contentWindow.addEventListener('resize', () => refresh());
  }

  // The element (and its layout path) a pointer is over, in this layout.
  function elementAt(doc, el) {
    const name = b.layout();
    for (let e = el; e && e !== doc.documentElement; e = e.parentElement) {
      const p = e.dataset?.jbPath;
      if (p !== undefined && (p === name || p.startsWith(`${name}/`))) {
        return { el: e, path: p === name ? [] : p.slice(name.length + 1).split('/').map(Number) };
      }
    }
    return null;
  }

  function elementFor(path) {
    const doc = frame?.contentDocument;
    if (!doc || !path) return null;
    const key = [b.layout(), ...path].join('/');
    return doc.querySelector(`[data-jb-path="${key}"]`);
  }

  function place(box, el) {
    if (!el) { box.hidden = true; return null; }
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) { box.hidden = true; return null; }
    box.hidden = false;
    Object.assign(box.style, {
      left: `${r.left * scale}px`, top: `${r.top * scale}px`,
      width: `${r.width * scale}px`, height: `${r.height * scale}px`,
    });
    return r;
  }

  // Redraws the hover and selection outlines (after scrolling, edits...).
  function refresh() {
    const sel = b.selected();
    const selEl = elementFor(sel);
    const r = place(selectBox, selEl);
    if (r) {
      selectBox.classList.toggle('pv-tools-below', r.top * scale < 34);
      drawTools(sel);
    }
    const sameAsSel = hoverPath && sel && hoverPath.join('/') === sel.join('/');
    const hr = place(hoverBox, !interact && hoverPath && !sameAsSel ? elementFor(hoverPath) : null);
    if (hr) {
      const node = at(b.tree(), hoverPath);
      hoverBox.firstChild.textContent = node ? (hoverPath.length ? describe(b.schema, node).label : b.rootLabel()) : '';
    }
  }

  function drawTools(path) {
    const tree = b.tree();
    const node = at(tree, path);
    if (!node) return;
    const label = path.length ? describe(b.schema, node).label : b.rootLabel();
    const parent = path.length ? at(tree, path.slice(0, -1)) : null;
    const i = path[path.length - 1];
    tools.replaceChildren(
      h('span.pv-name', label),
      ...(path.length ? [
        iconButton('arrow-up-left', 'Select the container it\'s in', { onclick: () => b.select(path.slice(0, -1)) }),
        iconButton('chevron-up', 'Move up', { disabled: i === 0, onclick: () => b.moveBy(path, -1) }),
        iconButton('chevron-down', 'Move down', { disabled: i >= parent.children.length - 1, onclick: () => b.moveBy(path, 1) }),
        iconButton('copy', 'Duplicate', { disabled: !canDuplicate(tree, b.schema, b.layout(), path), onclick: () => b.duplicate(path) }),
        iconButton('trash-2', 'Delete', { disabled: !canDelete(tree, b.schema, b.layout(), path), onclick: () => b.remove(path) }),
      ] : []));
  }

  // Dropping onto the preview --------------------------------------------------

  const surface = {
    hit(x, y, payload) {
      const doc = frame?.contentDocument;
      const fr = frameBox.getBoundingClientRect();
      if (!doc || x < fr.left || x > fr.right || y < fr.top || y > fr.bottom) { surface.clear(); return null; }
      const ix = (x - fr.left) / scale;
      const iy = (y - fr.top) / scale;
      const t = dropTarget(doc, ix, iy, payload);
      showDrop(t);
      return t;
    },
    clear() { dropLine.hidden = true; },
  };

  function dropTarget(doc, ix, iy, payload) {
    const tree = b.tree();
    const hit = elementAt(doc, doc.elementFromPoint(ix, iy));
    if (!hit) return null;
    let container = hit;
    let node = at(tree, hit.path);
    if (!node) return null;
    if (!isContainer(node) || nearEdge(hit.el, ix, iy)) {
      if (!hit.path.length) return null;
      const parentPath = hit.path.slice(0, -1);
      container = { el: elementFor(parentPath), path: parentPath };
      if (!container.el) return null;
    }
    const kids = childElements(container.el, container.path);
    let index = kids.filter(k => before(k.getBoundingClientRect(), ix, iy)).length;
    // The engine skips blocks it doesn't draw: count layout children, not elements.
    const pathIndex = index < kids.length ? Number(kids[index].dataset.jbPath.split('/').pop()) : at(tree, container.path).children.length;
    if (!canDrop(tree, b.schema, b.layout(), payload.node, payload.from, container.path)) return null;
    return { parentPath: container.path, index: pathIndex, el: container.el, kids, at: index, flow: flowOf(container.el) };
  }

  function nearEdge(el, ix, iy) {
    const r = el.getBoundingClientRect();
    const m = Math.min(18, r.width * 0.12, r.height * 0.12);
    return ix < r.left + m || ix > r.right - m || iy < r.top + m || iy > r.bottom - m;
  }

  function childElements(el, path) {
    const key = [b.layout(), ...path].join('/');
    return [...el.querySelectorAll('[data-jb-path]')].filter(k => {
      const p = k.dataset.jbPath;
      return p.slice(0, p.lastIndexOf('/')) === key && (k.offsetWidth || k.offsetHeight);
    });
  }

  // Reading order: a child comes before the pointer if it ends above it, or
  // shares its line and sits to the left.
  function before(r, x, y) {
    if (r.bottom <= y) return true;
    if (r.top > y) return false;
    return r.left + r.width / 2 < x;
  }

  function flowOf(el) {
    const s = el.ownerDocument.defaultView.getComputedStyle(el);
    if (s.display.includes('flex')) return s.flexDirection.startsWith('row') ? 'x' : 'y';
    if (s.display.includes('grid')) return s.gridTemplateColumns.split(' ').length > 1 ? 'x' : 'y';
    return 'y';
  }

  function showDrop(t) {
    if (!t) { dropLine.hidden = true; return; }
    dropLine.hidden = false;
    dropLine.className = 'pv-drop';
    const S = v => `${v * scale}px`;
    if (!t.kids.length) {
      const r = t.el.getBoundingClientRect();
      dropLine.classList.add('pv-drop-box');
      Object.assign(dropLine.style, { left: S(r.left), top: S(r.top), width: S(r.width), height: S(Math.max(r.height, 8)) });
      return;
    }
    const ref = t.kids[Math.min(t.at, t.kids.length - 1)].getBoundingClientRect();
    const after = t.at >= t.kids.length;
    if (t.flow === 'x') {
      const x = after ? ref.right : ref.left;
      Object.assign(dropLine.style, { left: S(x - 2), top: S(ref.top), width: '4px', height: S(ref.height) });
    } else {
      const y = after ? ref.bottom : ref.top;
      Object.assign(dropLine.style, { left: S(ref.left), top: S(y - 2), width: S(ref.width), height: '4px' });
    }
  }

  return {
    el: stage,
    reload,
    refresh,
    surface,
    setDevice(key) { device = DEVICES[key] || DEVICES['16:9']; fit(); },
    setInteract(on) { interact = on; hoverPath = null; stage.classList.toggle('interact', on); refresh(); },
    reveal(path) {
      const el = elementFor(path);
      if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      setTimeout(refresh, 350);
    },
    fit,
  };
}
