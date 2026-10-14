// Turns a layout tree (docs/theme-format.md, "Layouts") into page elements.
// A container becomes a <div>; a block becomes its <jb-…> element with its
// settings as attributes, then the block's own code fills it.
//
// `path` (the admin preview only) tags every element with where its node
// sits in the layout -- "home/0/2" is the third child of the first child of
// home -- so the page builder can tell which node a click landed on.

import { BLOCKS, fitShape } from './blocks.js';

export function renderLayout(node, ctx, path) {
  if ('block' in node) return renderBlock(node, ctx, path);

  const div = document.createElement('div');
  div.className = ['jb-container', node.class].filter(Boolean).join(' ');
  setAttributes(div, node, ['children', 'class']);
  if (path) div.dataset.jbPath = path;
  node.children.forEach((child, i) => {
    const el = renderLayout(child, ctx, path && `${path}/${i}`);
    if (el) div.append(el);
  });
  return div;
}

function renderBlock(node, ctx, path) {
  const fill = BLOCKS[node.block];
  if (fill === undefined) return null;  // newer than this engine; skipped

  const el = document.createElement(`jb-${node.block}`);
  if (node.class) el.className = node.class;
  setAttributes(el, node, ['block', 'class']);
  if (path) el.dataset.jbPath = path;
  if (fill) {
    fill(el, node, ctx);
    fitShape(el, node);
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
