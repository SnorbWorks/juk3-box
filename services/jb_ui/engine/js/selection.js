// The selection format: how a song's code is written on this jukebox
// (letters and numbers, or numbers only). The board numbers its slots by
// it, so its codes always match what the keys can enter. The Python twin is
// shared/selection.py (jb-core's entry uses it); keep them alike.

import { store } from './store.js';

const KEYPAD_DIGITS = '1234567890';   // 1-9, then 0
export const DEFAULT = { kind: 'letters', letters: 'ABCDEFGHJK', digits: '1234567890' };

// The owner's format (jb-ui sends it cleaned, in settings) as codes and groups.
export function format(spec = store.get('settings')?.selection || DEFAULT) {
  let codes;
  if (spec.kind === 'numbers') {
    codes = [];
    for (let n = spec.first; n <= spec.last; n++) {
      const c = String(n).padStart(spec.places, '0');
      if ([...c].every((ch, i) => spec.place_digits[i].includes(ch))) codes.push(c);
    }
  } else {
    codes = [...spec.letters].flatMap(l => [...spec.digits].map(d => l + d));
  }
  // Panels: a letter's codes, or ten numbers.
  const byKey = new Map();
  for (const c of codes) {
    const k = spec.kind === 'numbers' ? c.slice(0, -1) : c[0];
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(c);
  }
  const groups = [...byKey.values()].map(g => ({
    label: spec.kind === 'numbers' ? `${g[0]}–${g[g.length - 1]}` : g[0][0],
    codes: g,
  }));
  return { spec, kind: spec.kind, codes, groups };
}

// The digits a format uses, in keypad order.
export function formatDigits(spec) {
  const used = spec.kind === 'numbers' ? spec.place_digits.join('') : spec.digits;
  return [...KEYPAD_DIGITS].filter(d => used.includes(d)).join('');
}
