// Media library: pictures for Image and Logo blocks. Also used as the
// picker inside the page builder.

import { get, del, upload } from '../api.js';
import { button, bytes, clear, confirmDialog, emptyState, h, icon, iconButton, modal, pageHeader, showError, toast } from '../ui.js';

export async function show(main) {
  const grid = h('div');
  const input = fileInput(files => uploadAll(files, () => refresh()));
  const zone = dropzone(files => uploadAll(files, () => refresh()), input);
  clear(main,
    pageHeader('Media', 'Pictures you can place on the cabinet\'s screen with the page builder.',
      button('Upload pictures', { variant: 'primary', onclick: () => input.click() }, 'upload'), input),
    h('div.stack', zone, grid));
  const refresh = async () => {
    const { media } = await get('/api/media');
    if (!media.length) {
      clear(grid, emptyState('image', 'No pictures yet', 'Upload JPEG, PNG, GIF or WebP pictures up to 25 MB.'));
      return;
    }
    clear(grid, h('div.media-grid', media.map(m => h('div.media-item',
      h('img', { src: m.url, alt: '', loading: 'lazy' }),
      h('span.media-name', { title: m.name }, `${m.name} · ${bytes(m.size)}`),
      iconButton('trash-2', `Delete ${m.name}`, { class: 'media-del', onclick: async () => {
        if (!await confirmDialog('Delete this picture?', `${m.name} will be removed. Blocks using it will show nothing.`, { confirm: 'Delete', danger: true })) return;
        try { await del(`/api/media/${encodeURIComponent(m.name)}`); refresh(); } catch (e) { showError(e); }
      } })))));
  };
  await refresh();
}

function fileInput(onFiles) {
  const input = h('input', { type: 'file', accept: 'image/jpeg,image/png,image/gif,image/webp', multiple: true, hidden: true,
    onchange: () => { onFiles([...input.files]); input.value = ''; } });
  return input;
}

function dropzone(onFiles, input) {
  const zone = h('div.dropzone', icon('upload', 'big'), h('div', 'Drop pictures here, or ', h('a', { href: '#', onclick: e => { e.preventDefault(); input.click(); } }, 'choose files')));
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('over');
    onFiles([...e.dataTransfer.files].filter(f => f.type.startsWith('image/')));
  });
  return zone;
}

async function uploadAll(files, done) {
  let ok = 0;
  for (const f of files) {
    try {
      await upload('/api/media', f);
      ok++;
    } catch (e) { showError(e); }
  }
  if (ok) toast(`${ok} picture${ok === 1 ? '' : 's'} uploaded.`, 'success');
  done?.();
}

// The picker: the media library and the theme's own pictures. Resolves to
// a setting value ("/media/x.jpg" or a theme path), or null if cancelled.
export function pickPicture(themeId, current) {
  return new Promise(resolve => {
    let chosen = current || null;
    let answered = false;
    const lib = h('div');
    const themed = h('div');
    const tabs = h('div.segmented');
    const panes = { library: lib, theme: themed };
    const showTab = name => {
      for (const b of tabs.children) b.setAttribute('aria-pressed', String(b.dataset.tab === name));
      for (const [k, p] of Object.entries(panes)) p.hidden = k !== name;
    };
    for (const [id, label] of [['library', 'Media library'], ['theme', 'Theme pictures']]) {
      tabs.append(h('button', { type: 'button', dataset: { tab: id }, onclick: () => showTab(id) }, label));
    }
    const items = [];
    const item = (src, url, name) => {
      const el = h('button.media-item', { type: 'button', 'aria-pressed': String(src === chosen), onclick: () => {
        chosen = src;
        for (const i of items) i.setAttribute('aria-pressed', String(i === el));
      } }, h('img', { src: url, alt: '', loading: 'lazy' }), h('span.media-name', name));
      items.push(el);
      return el;
    };
    const input = fileInput(files => uploadAll(files, loadLib));
    async function loadLib() {
      const { media } = await get('/api/media');
      clear(lib, h('div.stack',
        button('Upload pictures', { variant: 'small', onclick: () => input.click() }, 'upload'), input,
        media.length ? h('div.media-grid', media.map(m => item(m.src, m.url, m.name)))
          : emptyState('image', 'No pictures yet', 'Upload one to use it here.')));
    }
    async function loadTheme() {
      const { pictures } = await get(`/api/themes/${encodeURIComponent(themeId)}/pictures`);
      clear(themed, pictures.length ? h('div.media-grid', pictures.map(p => item(p.src, p.url, p.name)))
        : emptyState('image', 'This theme has no pictures'));
    }
    loadLib().catch(showError);
    loadTheme().catch(showError);
    showTab(current && !String(current).startsWith('/media/') ? 'theme' : 'library');
    modal({
      title: 'Choose a picture', wide: true,
      body: h('div.stack', tabs, lib, themed),
      onClose: () => { if (!answered) resolve(null); },
      actions: [
        { label: 'Cancel', onclick: () => { answered = true; resolve(null); } },
        { label: 'Use picture', variant: 'primary', onclick: () => { answered = true; resolve(chosen); } },
      ],
    });
  });
}
