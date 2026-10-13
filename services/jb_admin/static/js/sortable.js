// Drag to reorder a list, with a mouse or a finger. Items carry a handle
// ([data-handle]); dragging it moves the item, and onMove(from, to) reports
// the change when the drag ends. Pointer events, not HTML5 drag and drop,
// which doesn't work on phones.

export function sortable(list, onMove) {
  list.addEventListener('pointerdown', e => {
    const handle = e.target.closest('[data-handle]');
    const item = handle?.closest('li');
    if (!item || item.parentElement !== list || e.button > 0) return;
    e.preventDefault();
    const items = [...list.children];
    const from = items.indexOf(item);
    const startY = e.clientY;
    const rects = items.map(i => i.getBoundingClientRect());
    let to = from;
    item.classList.add('dragging');
    handle.setPointerCapture(e.pointerId);

    const move = ev => {
      const dy = ev.clientY - startY;
      item.style.transform = `translateY(${dy}px)`;
      const mid = rects[from].top + rects[from].height / 2 + dy;
      to = rects.findIndex(r => mid < r.top + r.height / 2);
      if (to === -1) to = items.length - 1;
      else if (to > from) to -= 1;
      // Make room: items between move by the dragged item's height.
      const h = rects[from].height;
      items.forEach((other, i) => {
        if (other === item) return;
        let shift = 0;
        if (from < to && i > from && i <= to) shift = -h;
        if (from > to && i >= to && i < from) shift = h;
        other.style.transform = shift ? `translateY(${shift}px)` : '';
        other.style.transition = 'transform .12s';
      });
    };
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      item.classList.remove('dragging');
      for (const i of items) { i.style.transform = ''; i.style.transition = ''; }
      if (to !== from) onMove(from, to);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
}
