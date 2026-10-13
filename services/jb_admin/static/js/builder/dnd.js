// Drag and drop for the page builder, with a mouse or a finger.
//
// A drag carries what's being dragged (a node already in the layout, or a
// new block from the palette). Drop surfaces (the structure tree, the live
// preview) are asked, as the pointer moves, where a drop would land; the
// first that answers shows its marker. Pointer events throughout: HTML5
// drag and drop doesn't work on phones.

import { h, icon } from '../ui.js';

const START_PX = 5;

export function dragFrom(event, { label, iconName, payload, surfaces, onDrop, onStart, immediate = false }) {
  if (event.button > 0) return;
  const el = event.currentTarget;
  const startX = event.clientX;
  const startY = event.clientY;
  let started = false;
  let ghost = null;
  let target = null;
  let surface = null;
  const pointerId = event.pointerId;
  try { el.setPointerCapture(pointerId); } catch { /* already released */ }

  const begin = () => {
    started = true;
    document.body.classList.add('jb-dragging');
    ghost = h('div.drag-ghost', icon(iconName || 'box'), h('span', label));
    ghost.style.transform = `translate(${startX + 12}px, ${startY + 12}px)`;
    document.body.append(ghost);
    onStart?.();
  };

  const move = e => {
    if (e.pointerId !== pointerId) return;
    if (!started) {
      if (!immediate && Math.hypot(e.clientX - startX, e.clientY - startY) < START_PX) return;
      begin();
    }
    e.preventDefault();
    ghost.style.transform = `translate(${e.clientX + 12}px, ${e.clientY + 12}px)`;
    let found = null;
    let from = null;
    for (const s of surfaces) {
      found = s.hit(e.clientX, e.clientY, payload);
      if (found) { from = s; break; }
    }
    if (surface && surface !== from) surface.clear();
    surface = from;
    target = found;
    ghost.classList.toggle('no-drop', !found);
  };

  const end = e => {
    if (e.pointerId !== pointerId) return;
    el.removeEventListener('pointermove', move);
    el.removeEventListener('pointerup', end);
    el.removeEventListener('pointercancel', end);
    if (!started) return;
    ghost.remove();
    document.body.classList.remove('jb-dragging');
    surface?.clear();
    if (e.type === 'pointerup' && target) onDrop(target);
    // The click that follows a drag mustn't also select or add.
    const swallow = ev => { ev.stopPropagation(); ev.preventDefault(); };
    el.addEventListener('click', swallow, { capture: true, once: true });
    setTimeout(() => el.removeEventListener('click', swallow, { capture: true }), 0);
  };

  el.addEventListener('pointermove', move);
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
  if (immediate) begin();
}

// Scrolls `box` when the pointer is near its top or bottom edge.
export function edgeScroll(box, y) {
  const r = box.getBoundingClientRect();
  const zone = 36;
  if (y < r.top + zone) box.scrollTop -= Math.ceil((r.top + zone - y) / 3);
  else if (y > r.bottom - zone) box.scrollTop += Math.ceil((y - (r.bottom - zone)) / 3);
}
