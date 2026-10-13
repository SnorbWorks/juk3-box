// Account: change the owner password.

import { post } from '../api.js';
import { button, clear, field, h, pageHeader, showError, toast } from '../ui.js';

export function show(main) {
  const current = h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const next = h('input', { type: 'password', autocomplete: 'new-password', required: true, minLength: 8 });
  const again = h('input', { type: 'password', autocomplete: 'new-password', required: true });
  const submit = button('Change password', { variant: 'primary', type: 'submit' });
  const form = h('form.card.card-pad', {
    style: { maxWidth: '460px' },
    onsubmit: async e => {
      e.preventDefault();
      if (next.value !== again.value) {
        toast('The new passwords don\'t match.', 'error');
        return;
      }
      submit.disabled = true;
      try {
        await post('/api/password', { current: current.value, new: next.value });
        toast('Password changed. Other devices need to log in again.', 'success');
        form.reset();
      } catch (err) { showError(err); }
      submit.disabled = false;
    },
  },
  h('h2', { style: { marginBottom: '14px' } }, 'Password'),
  field('Current password', current),
  field('New password', next, 'At least 8 characters.'),
  field('Type it again', again),
  h('div', { style: { marginTop: '18px' } }, submit));
  clear(main, pageHeader('Account', 'There\'s one owner login for the admin.'), form);
}
