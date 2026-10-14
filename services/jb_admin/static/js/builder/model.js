// The layout being edited: a tree of containers ({children: [...]}) and
// blocks ({block: "name", ...settings}), exactly the JSON a theme's
// layouts/*.json holds (docs/theme-format.md, Layouts). Paths are arrays of
// child indexes from the root: [] is the root, [0, 2] its first child's third.

export const isContainer = node => Array.isArray(node?.children);

export function at(tree, path) {
  let node = tree;
  for (const i of path) {
    node = node?.children?.[i];
  }
  return node ?? null;
}

export function pathOf(tree, target, path = []) {
  if (tree === target) return path;
  if (!isContainer(tree)) return null;
  for (let i = 0; i < tree.children.length; i++) {
    const found = pathOf(tree.children[i], target, [...path, i]);
    if (found) return found;
  }
  return null;
}

export function parentOf(tree, path) {
  return path.length ? at(tree, path.slice(0, -1)) : null;
}

export function walk(node, fn, path = []) {
  fn(node, path);
  if (isContainer(node)) node.children.forEach((c, i) => walk(c, fn, [...path, i]));
}

export function count(tree, blockName) {
  let n = 0;
  walk(tree, node => { if (node.block === blockName) n++; });
  return n;
}

export function samePath(a, b) {
  return a && b && a.length === b.length && a.every((v, i) => v === b[i]);
}

// Is `inner` inside (or the same as) `outer`?
export function within(inner, outer) {
  return outer.length <= inner.length && outer.every((v, i) => v === inner[i]);
}

export function remove(tree, path) {
  const parent = parentOf(tree, path);
  return parent.children.splice(path[path.length - 1], 1)[0];
}

export function insert(tree, parentPath, index, node) {
  const parent = at(tree, parentPath);
  parent.children.splice(Math.max(0, Math.min(index, parent.children.length)), 0, node);
  return [...parentPath, Math.max(0, Math.min(index, parent.children.length - 1))];
}

// Moves the node at `from` to (parentPath, index), where index counts the
// destination's children as they are before the move. Returns its new path.
export function move(tree, from, parentPath, index) {
  const node = at(tree, from);
  const fromParent = from.slice(0, -1);
  const fromIndex = from[from.length - 1];
  // Removing it first shifts later siblings in the same parent, and any
  // destination path that runs through a later sibling.
  let dest = [...parentPath];
  if (samePath(fromParent, parentPath) && index > fromIndex) index -= 1;
  if (dest.length > fromParent.length && within(dest, fromParent) && dest[fromParent.length] > fromIndex) {
    dest[fromParent.length] -= 1;
  }
  remove(tree, from);
  return insert(tree, dest, index, node);
}

export const clone = node => JSON.parse(JSON.stringify(node));

// What a drop would do is allowed? `node` is what's dropped, `from` its
// current path (null if new), `layout` the layout's name.
export function canDrop(tree, schema, layout, node, from, parentPath) {
  const parent = at(tree, parentPath);
  if (!isContainer(parent)) return false;
  if (from && within(parentPath, from)) return false;           // into itself
  return canHave(tree, schema, layout, node, !from);
}

// May this layout hold `node` (and everything inside it)? `adding` counts it
// as an extra copy (for blocks that may appear only once).
export function canHave(tree, schema, layout, node, adding) {
  let ok = true;
  walk(node, n => {
    if (!n.block) return;
    const def = schema.blocks[n.block];
    if (def?.only_in && !def.only_in.includes(layout)) ok = false;
    if (def?.unique && adding && count(tree, n.block) >= 1) ok = false;
  });
  return ok;
}

// Can this node be deleted without breaking the layout?
export function canDelete(tree, schema, layout, path) {
  if (!path.length) return false;
  let ok = true;
  walk(at(tree, path), n => {
    const def = n.block && schema.blocks[n.block];
    if (def?.required_in?.includes(layout) && count(tree, n.block) <= 1) ok = false;
  });
  return ok;
}

export function canDuplicate(tree, schema, layout, path) {
  return path.length > 0 && canHave(tree, schema, layout, at(tree, path), true);
}

// A new block from the palette: its defaults (or a preset's values).
export function newBlock(schema, name, presetValues) {
  if (name === '$container') return { class: 'jb-u-column', children: [] };
  const node = { block: name };
  for (const [key, spec] of Object.entries(schema.blocks[name]?.settings || {})) {
    if (spec.default !== undefined && spec.default !== '' && spec.type !== 'bool') node[key] = spec.default;
  }
  return Object.assign(node, presetValues || {});
}

// How a node reads in the tree: "Card row · Recents", "Heading · Welcome".
export function describe(schema, node) {
  const d = describeNode(schema, node);
  // Its width in a row, first: "1/3 · Featured artist".
  const span = schema.common?.span?.options.find(o => o.value && o.value === node.span)?.label;
  return span ? { ...d, detail: [span, d.detail].filter(Boolean).join(' · ') } : d;
}

function describeNode(schema, node) {
  if (isContainer(node)) {
    const helpers = Object.fromEntries((schema.container.helpers || []).map(x => [x.value, x.label]));
    const classes = (node.class || '').split(/\s+/).filter(Boolean);
    const arrange = classes.map(c => helpers[c]).find(Boolean);
    const own = classes.filter(c => !helpers[c]);
    return {
      label: arrange ? `${arrange}` : 'Container',
      detail: own.length ? own.map(c => `.${c}`).join(' ') : (node.focus ? `knob: ${node.focus}` : ''),
      icon: arrange?.startsWith('Grid') ? 'layout-grid' : arrange === 'Row' ? 'columns-3' : 'square-dashed',
    };
  }
  const def = schema.blocks[node.block];
  if (!def) return { label: node.block, detail: 'Not known to this engine', icon: 'circle-alert', unknown: true };
  let detail = '';
  if (node.block === 'section') {
    const type = def.settings.type.options.find(o => o.value === node.type)?.label || node.type;
    detail = [node.title, type].filter(Boolean).join(' · ');
    if (node.show === false || node.show === 'false') detail += ' (hidden)';
  } else if (node.block === 'heading' || node.block === 'text') {
    detail = String(node.text ?? '').slice(0, 40);
  } else if (node.block === 'image' || node.block === 'logo') {
    detail = node.src ? String(node.src).split('/').pop() : '';
  } else if (node.block === 'up-next' && node.limit) {
    detail = `${node.limit} songs`;
  }
  return { label: def.label, detail, icon: ICONS[def.icon] || 'box' };
}

// Block icon names in the schema -> Lucide icons.
export const ICONS = {
  heading: 'heading', text: 'type', image: 'image', spacer: 'move-vertical', rows: 'rows-3',
  'now-playing': 'disc-3', queue: 'list-music', board: 'grid-2x2', grid: 'layout-grid', info: 'info',
  pager: 'arrow-up-down', logo: 'sparkles', nav: 'navigation', header: 'heading', phone: 'smartphone',
  screen: 'app-window',
};
