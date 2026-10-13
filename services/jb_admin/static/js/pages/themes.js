// Themes: pick the cabinet's look, preview any theme live, install one
// from a zip, delete ones you added.

import { get, post, del, upload } from '../api.js';
import { button, clear, confirmDialog, h, icon, modal, pageHeader, showError, toast } from '../ui.js';

let main = null;

export async function show(el) {
  main = el;
  await render();
}

async function render() {
  const data = await get('/api/themes');
  const fileInput = h('input', { type: 'file', accept: '.zip,application/zip', hidden: true, onchange: () => {
    if (fileInput.files[0]) install(fileInput.files[0]);
    fileInput.value = '';
  } });
  clear(main,
    pageHeader('Themes', 'A theme decides how the cabinet\'s screen looks. Your page builder changes are kept per theme.',
      button('Upload theme', { variant: 'primary', onclick: () => fileInput.click() }, 'upload'), fileInput),
    h('div.grid.cols-3', data.themes.map(themeCard)));
}

function themeCard(t) {
  const broken = t.problems.length > 0;
  const shot = h('div.theme-shot', t.preview ? h('img', { src: t.preview, alt: '' }) : h('span', t.name));
  return h(`div.card.theme-card${t.active ? '.active' : ''}`,
    shot,
    h('div.theme-info',
      h('div.theme-title', h('h3', t.name),
        t.active ? h('span.badge.accent', icon('check'), 'Active') : null,
        t.shipped ? h('span.badge', 'Built in') : null,
        broken ? h('span.badge.danger', 'Can\'t load') : null),
      h('div.theme-meta', [t.version && `Version ${t.version}`, t.author && `by ${t.author}`].filter(Boolean).join(' · ')),
      t.description ? h('p.muted', t.description) : null,
      broken ? h('div.notice.danger', icon('circle-alert'), h('span', t.problems.join(' '))) : null),
    h('div.theme-actions',
      !t.active && !broken ? button('Activate', { variant: 'primary small', onclick: () => activate(t) }) : null,
      !broken ? button('Preview', { variant: 'small', onclick: () => preview(t) }, 'eye') : null,
      !broken ? button('Customize', { variant: 'small', onclick: () => { location.hash = `#/builder/home/${t.id}`; } }, 'layout-template') : null,
      !t.shipped && !t.active ? button('Delete', { variant: 'small danger', onclick: () => remove(t) }, 'trash-2') : null));
}

async function activate(t) {
  if (!await confirmDialog(`Use ${t.name}?`, 'The cabinet\'s screen will reload with this theme.', { confirm: 'Activate' })) return;
  try {
    await post('/api/themes/activate', { id: t.id });
    toast(`${t.name} is now on the cabinet.`, 'success');
    render();
  } catch (e) { showError(e); }
}

function preview(t) {
  const frame = h('iframe', { src: `/preview?preview=1&theme=${encodeURIComponent(t.id)}`, title: `${t.name} preview` });
  const holder = h('div.preview-modal', h('div.preview-scale', frame));
  const m = modal({ title: `Preview: ${t.name}`, wide: true, body: holder, actions: [
    { label: 'Close' },
    ...(t.active ? [] : [{ label: 'Activate', variant: 'primary', onclick: () => activate(t) }]),
  ] });
  // A 1920 x 1080 screen, scaled to fit the dialog.
  const fit = () => {
    const w = holder.clientWidth;
    frame.style.transform = `scale(${w / 1920})`;
    holder.style.height = `${(w / 1920) * 1080}px`;
  };
  requestAnimationFrame(fit);
  new ResizeObserver(fit).observe(holder);
  return m;
}

async function remove(t) {
  if (!await confirmDialog(`Delete ${t.name}?`, 'The theme and your page builder changes for it are removed. This can\'t be undone.', { confirm: 'Delete', danger: true })) return;
  try {
    await del(`/api/themes/${encodeURIComponent(t.id)}`);
    toast(`${t.name} deleted.`, 'success');
    render();
  } catch (e) { showError(e); }
}

async function install(file, replace = false) {
  const bar = h('div');
  const status = h('p.muted', `Uploading ${file.name}…`);
  const m = modal({ title: 'Installing theme', body: h('div.stack', status, h('div.progress', bar)) });
  try {
    const info = await upload(`/api/themes/upload${replace ? '?replace=1' : ''}`, file, p => {
      bar.style.width = `${Math.round(p * 100)}%`;
      if (p >= 1) status.textContent = 'Checking the theme…';
    });
    m.close();
    const dropped = info.dropped?.length ? ` Left out ${info.dropped.length} file(s) a theme can't contain.` : '';
    toast(`${info.name} installed.${dropped}`, 'success', 6000);
    render();
  } catch (e) {
    m.close();
    if (e.status === 400 && /already installed/.test(e.message)) {
      if (await confirmDialog('Replace the theme?', `${e.message} Replace it with this one?`, { confirm: 'Replace' })) install(file, true);
      return;
    }
    showError(e);
  }
}
