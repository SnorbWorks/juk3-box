// Setup: where the setup wizard is (docs/Setup Wizard — Plan.md), a way
// back into it, and running it again. The wizard itself is its own page,
// /setup, made for a phone.

import { get, post } from '../api.js';
import { button, clear, confirmDialog, h, icon, pageHeader, showError } from '../ui.js';

export async function show(main) {
  const s = await get('/api/wizard-status');
  const open = h('a.btn.primary', { href: '/setup', target: '_blank', rel: 'noopener' },
    icon('wand-sparkles'), h('span', s.done ? 'Open setup' : 'Continue setup'));
  const again = button('Run setup again', {
    onclick: async () => {
      const ok = await confirmDialog('Run setup again?',
        'Setup starts over from the name. Your password, songs, phones and settings stay as they are until you change them in setup.',
        { confirm: 'Run setup again' });
      if (!ok) return;
      try {
        await post('/api/wizard-again', {});
        location.href = '/setup';
      } catch (e) { showError(e); }
    },
  }, 'rotate-ccw');
  const state = s.done
    ? h('div.notice', icon('circle-check'), h('span', 'Setup is finished.'))
    : h('div.notice', icon('info'), h('span', `${s.finished} of ${s.of} steps done. Next: ${s.step}.`));
  const summary = h('div.card.card-pad', { style: { maxWidth: '560px' } },
    h('h2', { style: { marginBottom: '12px' } }, 'What setup knows'),
    h('dl.setup-summary', s.summary.flatMap(r => [h('dt', r.k), h('dd', r.v)])));
  clear(main,
    pageHeader('Setup', 'The step-by-step setup: name, network, power, lights, sound and more.'),
    h('div.stack', state, h('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap' } }, open, s.done ? again : null), summary));
}
