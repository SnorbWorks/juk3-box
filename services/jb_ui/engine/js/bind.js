// Block markup: turning a template into elements, and filling those
// elements with data (docs/theme-format.md, "Block markup").
//
// Binding is repeatable: blocks build their markup once and re-bind it on
// every change, so nothing flickers and a theme's elements keep their
// state (e.g. CSS transitions).

export function fromTemplate(html) {
  const t = document.createElement('template');
  t.innerHTML = html || '';
  return t.content.cloneNode(true);
}

export function bind(root, data) {
  for (const el of own(root, '[data-text]')) {
    const v = data[el.dataset.text];
    const text = v === undefined || v === null ? '' : String(v);
    if (el.textContent !== text) el.textContent = text;
  }
  for (const el of own(root, '[data-src]')) {
    const v = data[el.dataset.src];
    if (v) {
      if (el.getAttribute('src') !== v) el.setAttribute('src', v);
    } else {
      el.removeAttribute('src');
    }
  }
  for (const el of own(root, '[data-if]')) el.hidden = !data[el.dataset.if];
  for (const el of own(root, '[data-if-not]')) el.hidden = !!data[el.dataset.ifNot];
}

export function slot(root, name) {
  return root.querySelector(`[data-slot="${name}"]`);
}

// Elements under root that belong to root's own template, not to cards
// placed in one of its slots (those are bound with their own data).
function own(root, selector) {
  return [...root.querySelectorAll(selector)].filter(el => {
    const s = el.closest('[data-slot]');
    return !s || s === root || !root.contains(s);
  });
}
