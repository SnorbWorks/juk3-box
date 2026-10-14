// Media library: pictures and videos for the cabinet's screen (Image and
// Logo blocks, Shortcut cards, the background). Uploads are made right
// for the Pi on the jukebox: big pictures scaled down, videos converted
// (shown here with their progress). Also the picker inside the page
// builder and Appearance.

import { get, del, upload } from '../api.js';
import { button, bytes, clear, confirmDialog, emptyState, h, icon, iconButton, modal, pageHeader, showError, toast } from '../ui.js';

const POLL_MS = 2000;
let poll = null;

export async function show(main) {
  const grid = h('div');
  const input = fileInput(true, files => uploadAll(files, () => refresh()));
  const zone = dropzone(true, files => uploadAll(files, () => refresh()), input);
  clear(main,
    pageHeader('Media', 'Pictures and videos for the cabinet\'s screen. They\'re made right for the jukebox as they upload: big pictures are scaled down, videos are converted so the Pi plays them smoothly.',
      button('Upload', { variant: 'primary', onclick: () => input.click() }, 'upload'), input),
    h('div.stack', zone, grid));
  const refresh = async () => {
    const { media } = await get('/api/media');
    clearTimeout(poll);
    if (media.some(m => m.state === 'optimizing')) poll = setTimeout(() => refresh().catch(() => {}), POLL_MS);
    if (!media.length) {
      clear(grid, emptyState('image', 'Nothing here yet', 'Upload JPEG, PNG, GIF or WebP pictures, or MP4, MOV, WebM or MKV videos.'));
      return;
    }
    clear(grid, h('div.media-grid', media.map(m => h('div.media-item', { class: m.state ? `media-${m.state}` : undefined },
      thumb(m),
      h('span.media-name', { title: m.name }, m.state === 'optimizing' ? `${m.name} · converting ${Math.round((m.progress || 0) * 100)}%`
        : m.state === 'failed' ? `${m.name} · couldn't convert` : `${m.name} · ${bytes(m.size)}`),
      iconButton('trash-2', `Delete ${m.name}`, { class: 'media-del', onclick: async () => {
        const what = m.kind === 'video' ? 'video' : 'picture';
        if (!await confirmDialog(`Delete this ${what}?`, `${m.name} will be removed. Anything using it will show nothing.`, { confirm: 'Delete', danger: true })) return;
        try { await del(`/api/media/${encodeURIComponent(m.name)}`); refresh(); } catch (e) { showError(e); }
      } })))));
  };
  await refresh();
}

export function leave() {
  clearTimeout(poll);
}

// A media item's picture: the image, a frame of the video, or progress.
function thumb(m) {
  if (m.state === 'optimizing') {
    return h('div.media-thumb.media-busy', h('span.spinner'),
      h('div.media-progress', h('div', { style: { width: `${Math.round((m.progress || 0) * 100)}%` } })));
  }
  if (m.state === 'failed') return h('div.media-thumb.media-busy', icon('circle-alert'), h('span.media-error', m.error || ''));
  if (m.kind === 'video') {
    return h('div.media-thumb', h('video', { src: `${m.url}#t=1`, muted: true, preload: 'metadata', playsInline: true }),
      h('span.media-badge', icon('play'), 'Video'));
  }
  return h('img', { src: m.url, alt: '', loading: 'lazy' });
}

function fileInput(videos, onFiles) {
  const accept = 'image/jpeg,image/png,image/gif,image/webp' + (videos ? ',video/mp4,video/quicktime,video/webm,video/x-matroska,.mkv,.m4v' : '');
  const input = h('input', { type: 'file', accept, multiple: true, hidden: true,
    onchange: () => { onFiles([...input.files]); input.value = ''; } });
  return input;
}

function dropzone(videos, onFiles, input) {
  const zone = h('div.dropzone', icon('upload', 'big'), h('div', `Drop ${videos ? 'pictures or videos' : 'pictures'} here, or `,
    h('a', { href: '#', onclick: e => { e.preventDefault(); input.click(); } }, 'choose files')));
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('over');
    onFiles([...e.dataTransfer.files].filter(f => f.type.startsWith('image/') || (videos && f.type.startsWith('video/'))));
  });
  return zone;
}

async function uploadAll(files, done) {
  let pictures = 0, videos = 0;
  for (const f of files) {
    try {
      const item = await upload('/api/media', f);
      if (item.kind === 'video') videos++; else pictures++;
    } catch (e) { showError(e); }
  }
  if (pictures) toast(`${pictures} picture${pictures === 1 ? '' : 's'} uploaded.`, 'success');
  if (videos) toast(`${videos} video${videos === 1 ? '' : 's'} uploaded. Converting for the jukebox; you can use ${videos === 1 ? 'it' : 'them'} when done.`, 'success', 6000);
  done?.();
}

// The picker: the media library and the theme's own files. Resolves to a
// setting value ("/media/x.jpg" or a theme path), or null if cancelled.
// { video: true } offers videos too.
export function pickPicture(themeId, current, { video = false } = {}) {
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
    for (const [id, label] of [['library', 'Media library'], ['theme', video ? 'The theme\'s' : 'Theme pictures']]) {
      tabs.append(h('button', { type: 'button', dataset: { tab: id }, onclick: () => showTab(id) }, label));
    }
    const items = [];
    const item = m => {
      const el = h('button.media-item', { type: 'button', 'aria-pressed': String(m.src === chosen), onclick: () => {
        chosen = m.src;
        for (const i of items) i.setAttribute('aria-pressed', String(i === el));
      } }, thumb(m), h('span.media-name', m.name));
      items.push(el);
      return el;
    };
    const input = fileInput(video, files => uploadAll(files, loadLib));
    let timer = null;
    async function loadLib() {
      const { media } = await get('/api/media');
      clearTimeout(timer);
      const usable = media.filter(m => video || m.kind !== 'video');
      const waiting = usable.filter(m => m.state === 'optimizing');
      if (waiting.length) timer = setTimeout(() => loadLib().catch(() => {}), POLL_MS);
      clear(lib, h('div.stack',
        button(video ? 'Upload a picture or video' : 'Upload pictures', { variant: 'small', onclick: () => input.click() }, 'upload'), input,
        waiting.length ? h('p.muted', `Converting ${waiting.map(m => m.name).join(', ')}… it can be chosen when it's done.`) : null,
        usable.some(m => !m.state) ? h('div.media-grid', usable.filter(m => !m.state).map(item))
          : emptyState('image', 'Nothing here yet', 'Upload one to use it here.')));
    }
    async function loadTheme() {
      const { pictures } = await get(`/api/themes/${encodeURIComponent(themeId)}/pictures${video ? '?video=1' : ''}`);
      clear(themed, pictures.length ? h('div.media-grid', pictures.map(item))
        : emptyState('image', 'This theme has none'));
    }
    loadLib().catch(showError);
    loadTheme().catch(showError);
    showTab(current && !String(current).startsWith('/media/') ? 'theme' : 'library');
    modal({
      title: video ? 'Choose a picture or video' : 'Choose a picture', wide: true,
      body: h('div.stack', tabs, lib, themed),
      onClose: () => { clearTimeout(timer); if (!answered) resolve(null); },
      actions: [
        { label: 'Cancel', onclick: () => { answered = true; resolve(null); } },
        { label: 'Use it', variant: 'primary', onclick: () => { answered = true; resolve(chosen); } },
      ],
    });
  });
}
