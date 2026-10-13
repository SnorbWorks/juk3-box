// The Structure panel: the layout as an outline. Drag a row by its handle
// to move it (into a container, between blocks); click to select; the
// row's buttons duplicate or delete.

import { h, icon, iconButton } from '../ui.js';
import { dragFrom, edgeScroll } from './dnd.js';
import { at, canDelete, canDrop, canDuplicate, describe, isContainer, samePath } from './model.js';

export function createTree(b) {
  // b: the builder -- tree(), schema, layout(), selected(), select(path),
  // moveNode(from, parentPath, index), addNode(node, parentPath, index),
  // duplicate(path), remove(path), surfaces(), rootLabel().
  const collapsed = new WeakSet();
  const list = h('div.tree', { role: 'tree', 'aria-label': 'Layout structure' });
  const marker = h('div.drop-line', { hidden: true });
  const box = h('div.tree-scroll', list, marker);
  let insideRow = null;

  function render() {
    const tree = b.tree();
    const rows = [];
    const add = (node, path, depth) => {
      const container = isContainer(node);
      const isRoot = path.length === 0;
      const d = isRoot ? { label: b.rootLabel(), detail: node.class ? `.${node.class.split(' ').join(' .')}` : '', icon: 'frame' } : describe(b.schema, node);
      const selected = samePath(path, b.selected());
      const open = container && !collapsed.has(node);
      const row = h(`div.tree-row${selected ? '.selected' : ''}${d.unknown ? '.unknown' : ''}${isRoot ? '.root' : ''}`, {
        role: 'treeitem', tabIndex: selected ? 0 : -1, 'aria-selected': String(selected),
        'aria-expanded': container ? String(open) : undefined,
        dataset: { path: path.join('.') }, style: { '--depth': depth },
        onclick: () => b.select(path),
        onkeydown: e => keys(e, path),
      },
      container && node.children.length && !isRoot
        ? h('button.tree-toggle', { type: 'button', 'aria-label': open ? 'Collapse' : 'Expand', tabIndex: -1, onclick: e => {
          e.stopPropagation();
          if (open) collapsed.add(node); else collapsed.delete(node);
          render();
        } }, icon(open ? 'chevron-down' : 'chevron-right'))
        : h('span.tree-toggle-space'),
      isRoot ? null : h('span.tree-grip', { title: 'Drag to move', onpointerdown: e => startMove(e, path, d) }, icon('grip-vertical')),
      h('span.tree-icon', icon(d.icon)),
      h('span.tree-text', h('span.tree-label', d.label), d.detail ? h('span.tree-detail', d.detail) : null),
      isRoot ? null : h('span.tree-actions',
        iconButton('copy', 'Duplicate', { tabIndex: -1, disabled: !canDuplicate(tree, b.schema, b.layout(), path),
          onclick: e => { e.stopPropagation(); b.duplicate(path); } }),
        iconButton('trash-2', 'Delete', { tabIndex: -1, disabled: !canDelete(tree, b.schema, b.layout(), path),
          onclick: e => { e.stopPropagation(); b.remove(path); } })));
      rows.push(row);
      if (open) {
        node.children.forEach((c, i) => add(c, [...path, i], depth + 1));
        if (!node.children.length) {
          rows.push(h('div.tree-row.tree-empty', { dataset: { parent: path.join('.') }, style: { '--depth': depth + 1 } },
            h('span.tree-toggle-space'), h('span', isRoot ? 'Empty: drag blocks here from Add' : 'Empty: drag blocks here')));
        }
      }
    };
    add(tree, [], 0);
    list.replaceChildren(...rows);
  }

  function keys(e, path) {
    const rows = [...list.querySelectorAll('.tree-row:not(.tree-empty)')];
    const i = rows.findIndex(r => r.dataset.path === path.join('.'));
    const go = j => { const r = rows[j]; if (r) { b.select(r.dataset.path ? r.dataset.path.split('.').map(Number) : []); } };
    if (e.key === 'ArrowDown') { e.preventDefault(); go(i + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); go(i - 1); }
    else if ((e.key === 'Delete' || e.key === 'Backspace') && path.length && canDelete(b.tree(), b.schema, b.layout(), path)) {
      e.preventDefault(); b.remove(path);
    }
  }

  function startMove(e, path, d) {
    e.stopPropagation();
    const node = at(b.tree(), path);
    dragFrom(e, {
      label: d.label, iconName: d.icon, payload: { node, from: path },
      surfaces: b.surfaces(),
      immediate: e.pointerType !== 'mouse',
      onDrop: t => b.moveNode(path, t.parentPath, t.index),
    });
  }

  // Where a drop at (x, y) would land, for the drag controller.
  const surface = {
    hit(x, y, payload) {
      const r = box.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) { surface.clear(); return null; }
      edgeScroll(box, y);
      const t = target(x, y, payload);
      show(t);
      return t;
    },
    clear() { show(null); },
  };

  function target(x, y, payload) {
    const tree = b.tree();
    const rows = [...list.children];
    let row = rows.find(r => { const rr = r.getBoundingClientRect(); return y >= rr.top && y < rr.bottom; });
    const ok = (parentPath, index, kind, rowEl, depth) =>
      canDrop(tree, b.schema, b.layout(), payload.node, payload.from, parentPath)
        ? { parentPath, index, kind, row: rowEl, depth } : null;
    if (!row) {
      // Below everything: the end of the root.
      const last = rows[rows.length - 1];
      return last && y >= last.getBoundingClientRect().bottom ? ok([], tree.children.length, 'after', last, 1) : null;
    }
    if (row.classList.contains('tree-empty')) {
      const parent = row.dataset.parent ? row.dataset.parent.split('.').map(Number) : [];
      return ok(parent, 0, 'inside', row, parent.length + 1);
    }
    const path = row.dataset.path ? row.dataset.path.split('.').map(Number) : [];
    const node = at(tree, path);
    const rr = row.getBoundingClientRect();
    const f = (y - rr.top) / rr.height;
    if (!path.length) return ok([], 0, 'before-first', row, 1);
    const parent = path.slice(0, -1);
    const index = path[path.length - 1];
    if (isContainer(node)) {
      const open = !collapsed.has(node) && node.children.length;
      if (f < 0.25) return ok(parent, index, 'before', row, path.length);
      if (f > 0.75) return open ? ok(path, 0, 'after', row, path.length + 1) : ok(parent, index + 1, 'after', row, path.length);
      return ok(path, node.children.length, 'inside', row, path.length + 1);
    }
    return f < 0.5 ? ok(parent, index, 'before', row, path.length) : ok(parent, index + 1, 'after', row, path.length);
  }

  function show(t) {
    insideRow?.classList.remove('drop-inside');
    insideRow = null;
    if (!t) { marker.hidden = true; return; }
    if (t.kind === 'inside') {
      insideRow = t.row.classList.contains('tree-empty') ? t.row : t.row;
      insideRow.classList.add('drop-inside');
      marker.hidden = true;
      return;
    }
    const top = t.kind === 'before' ? t.row.offsetTop : t.row.offsetTop + t.row.offsetHeight;
    marker.hidden = false;
    marker.style.top = `${top - 1}px`;
    marker.style.left = `calc(${t.depth} * var(--indent) + 8px)`;
  }

  return { el: box, render, surface, scrollToSelected() {
    list.querySelector('.tree-row.selected')?.scrollIntoView({ block: 'nearest' });
  } };
}
