// Setup: where the setup wizard is (docs/Setup Wizard — Plan.md), a way
// back into it, and running it again. The wizard itself is its own page,
// /setup, made for a phone.

import { del, get, post, put } from '../api.js';
import { button, clear, confirmDialog, field, h, icon, pageHeader, showError, switchControl, toast } from '../ui.js';

let updatePoll = null;

export async function show(main) {
  clearTimeout(updatePoll);
  const [s, upd] = await Promise.all([get('/api/wizard-status'), get('/api/update')]);
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
  // The cabinet's buttons, taught again (a new keypad, a key to fix).
  const controls = h('a.btn', { href: '/setup?step=controls', target: '_blank', rel: 'noopener' },
    icon('keyboard'), h('span', 'Teach the buttons again'));
  const state = s.done
    ? h('div.notice', icon('circle-check'), h('span', 'Setup is finished.'))
    : h('div.notice', icon('info'), h('span', `${s.finished} of ${s.of} steps done. Next: ${s.step}.`));
  const summary = h('div.card.card-pad', { style: { maxWidth: '560px' } },
    h('h2', { style: { marginBottom: '12px' } }, 'What setup knows'),
    h('dl.setup-summary', s.summary.flatMap(r => [h('dt', r.k), h('dd', r.v)])));
  clear(main,
    pageHeader('Setup', 'The step-by-step setup: name, network, power, lights, sound and more.'),
    h('div.stack', state, h('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap' } }, open, s.done ? again : null, controls), summary,
      updatesCard(upd, () => show(main)), lastfmCard(s.lastfm, () => show(main))));
}

// Updates from the project's releases (deploy/jb-update).
const PHASES = { downloading: 'Downloading the update…', checking: 'Checking it\'s genuine…', installing: 'Installing…',
  restarting: 'Restarting the jukebox…', 'rolling back': 'That didn\'t work; putting the last version back…' };

function when(t) {
  return t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'never';
}

function updatesCard(u, refresh) {
  const busy = u.phase || u.starting;
  if (busy) updatePoll = setTimeout(refresh, 5000);       // jb-admin itself restarts partway: the next try catches up
  let line;
  if (!u.current) line = u.note || 'A development copy: updated by deploying.';
  else if (busy) line = PHASES[u.phase] || 'Starting the update…';
  else if (u.available) line = `${u.latest} is ready (this jukebox has ${u.current}).`;
  else line = `Up to date: ${u.current}. Last checked ${when(u.checked_at)}.`;
  const act = async (what, msg) => {
    try { const r = await post(`/api/update/${what}`, {}); if (msg) toast(msg, 'success'); refresh(r); } catch (e) { showError(e); }
  };
  const updateNow = button('Update now', { variant: 'primary', onclick: async () => {
    const ok = await confirmDialog(`Update to ${u.latest}?`,
      'The jukebox stops for a few minutes while it installs, then restarts. If the new version has trouble, the old one comes back by itself.',
      { confirm: 'Update now' });
    if (ok) act('apply', 'Updating. This page catches up when it\'s done.');
  } }, 'download');
  return h('div.card.card-pad', { style: { maxWidth: '560px' } },
    h('h2', { style: { marginBottom: '8px' } }, 'Updates'),
    h('p', { style: { marginBottom: '12px' } }, line),
    u.error ? h('div.notice', icon('triangle-alert'), h('span', u.error)) : null,
    u.current ? h('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', margin: '12px 0' } },
      u.available && !busy ? updateNow : null,
      busy ? null : button('Check now', { onclick: () => act('check') }, 'refresh-cw')) : null,
    u.current ? switchControl(u.auto, async on => {
      try { await put('/api/update/auto', { on }); } catch (e) { showError(e); }
    }, 'Update automatically overnight') : null);
}

// The owner's Last.fm key: added here when setup skipped it, or changed.
function lastfmCard(lf, refresh) {
  const key = h('input', { type: 'text', autocomplete: 'off', spellcheck: false, autocapitalize: 'off',
    placeholder: lf.set ? `Saved key ends in ${lf.ends}` : '32 letters and numbers' });
  const save = button(lf.set ? 'Replace key' : 'Save key', { variant: 'primary', type: 'submit' });
  const remove = lf.set ? button('Remove key', {
    onclick: async () => {
      const ok = await confirmDialog('Remove the Last.fm key?',
        'The Top 100 stops filling and the light show stops reading genres.', { confirm: 'Remove', danger: true });
      if (!ok) return;
      try { await del('/api/lastfm'); toast('Last.fm key removed.', 'success'); refresh(); } catch (e) { showError(e); }
    },
  }) : null;
  return h('form.card.card-pad', {
    style: { maxWidth: '560px' },
    onsubmit: async e => {
      e.preventDefault();
      save.disabled = true;
      try {
        await put('/api/lastfm', { key: key.value.trim() });
        toast('Last.fm key saved.', 'success');
        refresh();
      } catch (err) { showError(err); save.disabled = false; }
    },
  },
  h('h2', { style: { marginBottom: '8px' } }, 'Last.fm'),
  h('p', { style: { marginBottom: '14px' } }, lf.set
    ? `On: the Top 100 and genre-aware light shows use your key (it ends in ${lf.ends}).`
    : 'Off: the Top 100 stays empty and the light show goes by each song\'s energy only.'),
  field('API key', key, h('span', 'Get a free one at ', h('a', { href: lf.get_key, target: '_blank', rel: 'noopener' }, 'Last.fm\'s API page'),
    '. Copy the API key, not the shared secret.')),
  h('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', marginTop: '14px' } }, save, remove));
}
