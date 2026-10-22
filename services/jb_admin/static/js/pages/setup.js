// Setup: where the setup wizard is (docs/Setup Wizard — Plan.md), a way
// back into it, and running it again. The wizard itself is its own page,
// /setup, made for a phone.

import { del, get, post, put } from '../api.js';
import { button, clear, confirmDialog, field, h, icon, pageHeader, showError, toast } from '../ui.js';

export async function show(main) {
  const [s, named] = await Promise.all([get('/api/wizard-status'), get('/api/jukebox-name')]);
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
      nameCard(named, () => show(main)), lastfmCard(s.lastfm, () => show(main))));
}

// The cabinet's one name: the app, AirPlay, Bluetooth and its address
// on the network (garage-jukebox.local), which moves the admin too.
// Matches network_name in setup_wizard.py (and the setup page's).
function networkName(name) {
  return name.normalize('NFKD').replace(/[^\x00-\x7f]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63).replace(/-+$/, '') || 'juk3box';
}

function nameCard(n, refresh) {
  const input = h('input', { type: 'text', value: n.name, maxLength: 40, required: true, autocomplete: 'off' });
  const save = button('Rename', { variant: 'primary', type: 'submit' }, 'pencil');
  const address = h('span');
  const preview = () => {
    const name = input.value.trim();
    const next = `${networkName(name)}.local`;
    // A cabinet set up before the address followed the name can be brought into line.
    if (next === n.address) address.textContent = `Its address is ${n.address}.`;
    else if (name === n.name) address.textContent = `Its address is still ${n.address}, from before it followed the name. Save to make it ${next}.`;
    else address.textContent = `Its address becomes ${next}.`;
    save.lastChild.textContent = name === n.name ? 'Save' : 'Rename';
    save.disabled = !name || (name === n.name && next === n.address);
  };
  input.addEventListener('input', preview);
  preview();
  return h('form.card.card-pad', {
    style: { maxWidth: '560px' },
    onsubmit: async e => {
      e.preventDefault();
      const name = input.value.trim();
      const next = `${networkName(name)}.local`;
      const moving = next !== n.address;
      const ok = await confirmDialog(name === n.name ? `Move the admin to ${next}?` : `Rename it ${name}?`, moving
        ? `Phones, AirPlay and Bluetooth show the name, and the admin moves to ${next}: old bookmarks stop working, and you sign in again there. Music playing over AirPlay pauses for a moment.`
        : 'Phones, AirPlay and Bluetooth show the new name. Music playing over AirPlay pauses for a moment.',
      { confirm: name === n.name ? 'Move it' : 'Rename' });
      if (!ok) return;
      save.disabled = true;
      try {
        const r = await put('/api/jukebox-name', { name });
        toast(`The jukebox is now ${r.name}.`, 'success');
        const here = location.hostname;
        if (here.endsWith('.local') && here !== r.address) {
          // Give the network a moment to learn the new address.
          setTimeout(() => { location.href = `${location.protocol}//${r.address}${location.port ? `:${location.port}` : ''}/#/setup`; }, 2500);
        } else refresh();
      } catch (err) { showError(err); preview(); }
    },
  },
  h('h2', { style: { marginBottom: '8px' } }, 'Name'),
  h('p', { style: { marginBottom: '14px' } }, 'The jukebox\'s one name: in the app on every phone, the AirPlay speaker list, Bluetooth, and its address on your network.'),
  field('Name', input, address),
  h('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', marginTop: '14px' } }, save));
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
