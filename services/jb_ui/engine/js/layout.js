// Turns a layout tree (docs/theme-format.md, "Layouts") into page elements.
// A container becomes a <div>; a block becomes its <jb-…> element with its
// settings as attributes, then the block's own code fills it.

import { BLOCKS } from './blocks.js';

export function renderLayout(node, ctx) {
  if ('block' in node) return renderBlock(node, ctx);

  const div = document.createElement('div');
  div.className = ['jb-container', node.class].filter(Boolean).join(' ');
  setAttributes(div, node, ['children', 'class']);
  for (const child of node.children) {
    const el = renderLayout(child, ctx);
    if (el) div.append(el);
  }
  return div;
}

function renderBlock(node, ctx) {
  const fill = BLOCKS[node.block];
  if (fill === undefined) return null;  // newer than this engine; skipped

  const el = document.createElement(`jb-${node.block}`);
  if (node.class) el.className = node.class;
  setAttributes(el, node, ['block', 'class']);
  if (fill) {
    fill(el, node, ctx);
  } else {
    el.dataset.placeholder = node.block;
  }
  return el;
}

function setAttributes(el, node, skip) {
  for (const [key, value] of Object.entries(node)) {
    if (!skip.includes(key)) el.setAttribute(key, String(value));
  }
}
