// The Add panel: every block this layout can take, grouped, plus layout
// containers. Click (or tap) to add after the selection; drag into the
// structure or straight onto the preview to place it exactly.

import { clear, h, icon } from '../ui.js';
import { dragFrom } from './dnd.js';
import { canHave, clone, ICONS, newBlock } from './model.js';

const CONTAINERS = [
  { label: 'Column', detail: 'Blocks stacked', icon: 'rows-2', make: () => ({ class: 'jb-u-column', children: [] }) },
  { label: 'Row', detail: 'Blocks side by side', icon: 'columns-3', make: () => ({ class: 'jb-u-row', children: [] }) },
  { label: 'Grid, 2 across', detail: 'Two columns', icon: 'layout-grid', make: () => ({ class: 'jb-u-grid-2', children: [] }) },
  { label: 'Grid, 3 across', detail: 'Three columns', icon: 'layout-grid', make: () => ({ class: 'jb-u-grid-3', children: [] }) },
];

export function createPalette(b) {
  // b: tree(), schema, layout(), addNode(node, parentPath?, index?), surfaces()
  const search = h('input.palette-search', { type: 'search', placeholder: 'Search blocks', 'aria-label': 'Search blocks', oninput: () => render() });
  const list = h('div.palette-list');
  const el = h('div.palette', search, list);

  function items() {
    const s = b.schema;
    const out = [];
    for (const [name, def] of Object.entries(s.blocks)) {
      if (def.only_in && !def.only_in.includes(b.layout())) continue;
      const base = { group: def.group, icon: ICONS[def.icon] || 'box', description: def.description };
      if (def.presets) {
        for (const p of def.presets) out.push({ ...base, label: p.label, detail: def.label, make: () => newBlock(s, name, p.values) });
      } else {
        out.push({ ...base, label: def.label, make: () => newBlock(s, name) });
      }
    }
    for (const p of s.patterns || []) {
      out.push({ group: 'patterns', label: p.label, detail: p.detail, description: p.detail, icon: p.icon || 'sparkles', make: () => clone(p.tree) });
    }
    for (const c of CONTAINERS) out.push({ ...c, group: 'structure', description: c.detail });
    return out;
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const all = items().filter(i => !q || `${i.label} ${i.detail || ''} ${i.description || ''}`.toLowerCase().includes(q));
    const groups = b.schema.groups.map(g => [g, all.filter(i => i.group === g.id)]).filter(([, l]) => l.length);
    clear(list, groups.length ? groups.map(([g, l]) => h('section.palette-group',
      h('h3.palette-head', g.label),
      h('div.palette-grid', l.map(tile)))) : h('p.muted.palette-none', 'No blocks match.'));
  }

  function tile(item) {
    const allowed = canHave(b.tree(), b.schema, b.layout(), item.make(), true);
    const t = h('button.palette-tile', {
      type: 'button', disabled: !allowed, title: allowed ? item.description : 'Already in this layout',
      onclick: () => b.addNode(item.make()),
      onpointerdown: e => {
        // A mouse drags the whole tile; a finger drags by the handle, so
        // the list still scrolls.
        if (e.pointerType === 'mouse' && allowed) startDrag(e, item, false);
      },
    },
    h('span.palette-icon', icon(item.icon)),
    h('span.palette-text', h('span.palette-label', item.label), item.detail ? h('span.palette-detail', item.detail) : null),
    allowed ? h('span.palette-grip', { title: 'Drag to place', onpointerdown: e => {
      if (e.pointerType !== 'mouse') { e.stopPropagation(); startDrag(e, item, true); }
    } }, icon('grip-vertical')) : null);
    return t;
  }

  function startDrag(e, item, immediate) {
    const node = item.make();
    dragFrom(e, {
      label: item.label, iconName: item.icon, payload: { node, from: null },
      surfaces: b.surfaces(), immediate,
      onDrop: t => b.addNode(node, t.parentPath, t.index),
    });
  }

  return { el, render };
}
