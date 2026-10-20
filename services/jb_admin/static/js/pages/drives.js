// Drives: USB drives plugged into the jukebox. Their songs play from the
// drive (jb-source-local) and join the phone's library; their pictures and
// videos are copied into the media library. Three tabs: the drives
// themselves, the songs that came in lately, and every drive song, where
// missing info can be filled in. The drive is read-only: edits are kept
// on the jukebox, on top of the file's own tags.

import { get, post } from '../api.js';
import { button, bytes, clear, confirmDialog, emptyState, field, h, icon, modal, pageHeader, showError, switchControl, toast } from '../ui.js';

const TABS = [['overview', 'Drives', 'usb'], ['new', 'New', 'sparkles'], ['songs', 'Songs', 'music']];
const SKIP_REASON = {
  protected: 'Copy-protected iTunes song (plays only in Apple\'s apps)',
  unreadable: 'Couldn\'t be read as music',
  'too big': 'Too big to import',
  'media library full': 'Media library full (raise the limit below)',
};
const NEW_LIMIT = 200;

let timer = null;
let body = null;
let tab = 'overview';
let data = null;
let lastJson = '';
let search = '';
let needsOnly = false;

export async function show(main) {
  const tabs = h('div.drv-tabs', { role: 'tablist' }, TABS.map(([id, label, ic]) => h('button.drv-tab', {
    type: 'button', role: 'tab', 'aria-selected': String(id === tab), dataset: { tab: id },
    onclick: () => {
      tab = id;
      for (const b of tabs.children) b.setAttribute('aria-selected', String(b.dataset.tab === id));
      render();
    },
  }, icon(ic), label)));
  body = h('div.stack');
  clear(main,
    pageHeader('Drives', 'Music, pictures and videos from USB drives. Songs play from the drive and join the phone\'s; nothing on the drive is ever changed.',
      button('Rescan', { onclick: () => act('rescan', {}, 'Reading the drives again…') }, 'refresh-cw')),
    tabs, body);
  await refresh(true);
  timer = setInterval(() => refresh(false), 3000);
}

export function leave() {
  clearInterval(timer);
  timer = null;
}

async function refresh(force) {
  try {
    data = await get('/api/drives');
  } catch (e) {
    clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  const json = JSON.stringify(data);
  if (!force && json === lastJson) return;
  lastJson = json;
  if (body?.isConnected) render();
}

async function act(action, payload = {}, done) {
  try {
    await post(`/api/drives/${action}`, payload);
    if (done) toast(done, 'ok');
    setTimeout(() => refresh(true), 800);
  } catch (e) {
    showError(e);
  }
}

function render() {
  if (!data) return;
  if (tab === 'overview') clear(body, ...overview());
  else if (tab === 'new') clear(body, newSongs());
  else renderSongs();
}

// -- Drives ----------------------------------------------------------------

const plugged = () => data.drives.filter(d => d.state === 'plugged in');

function overview() {
  const here = data.drives.filter(d => d.state !== 'not plugged in');
  const before = data.drives.filter(d => d.state === 'not plugged in');
  return [
    !data.bus ? h('div.notice.warn', icon('circle-alert'), h('span', 'The admin can\'t reach the jukebox right now; this may be out of date.')) : null,
    here.length ? null : emptyState('usb', 'No drive plugged in',
      'Plug a USB drive (exFAT, FAT, NTFS or Mac OS Extended) into one of the blue USB ports. Its music shows up here and on the cabinet within a few seconds.'),
    ...here.map(driveCard),
    importCard(),
    before.length ? h('div.card',
      h('div.card-head', h('h2', 'Seen before'), h('span.muted', 'Their play counts and edits are kept for when they\'re back')),
      h('div.ext-list', before.map(historyRow))) : null,
  ];
}

function driveCard(d) {
  if (d.state === 'ejected') {
    return h('div.card.card-pad.phone-row',
      h('div.stat-icon', icon('eject')),
      h('div.phone-text', h('div.stat-value', d.label || 'USB drive'), h('div.stat-sub', 'Ejected. Unplug it, or mount it again.')),
      h('div.phone-actions', button('Mount again', { variant: 'primary', onclick: () => act('mount', { id: d.id }, 'Mounting…') }, 'usb')));
  }
  const used = d.total ? Math.round((d.used / d.total) * 100) : null;
  const songSkips = (d.skipped || []);
  const mediaSkips = d.import?.skipped || [];
  const imp = d.import;
  return h('div.card',
    h('div.card-head',
      h('div.drv-title', icon('usb'), h('h2', d.label || 'USB drive'), d.scanning ? h('span.drv-badge', 'Reading…') : null),
      h('div.phone-actions',
        button('Eject', {
          onclick: async () => {
            const ok = await confirmDialog(`Eject ${d.label || 'the drive'}?`,
              'Its songs leave the screen, and any of them playing stops. Pulling it out without ejecting is safe too: the jukebox never writes to it.',
              { confirm: 'Eject' });
            if (ok) act('eject', { id: d.id }, 'Ejected. You can unplug it.');
          },
        }, 'eject'))),
    h('div.card-pad.drv-body',
      h('div.drv-stats',
        stat('Songs', String(d.song_count ?? 0), d.plays ? `${d.plays} plays on this jukebox` : 'Not played yet'),
        stat('Pictures & videos', `${d.pictures || 0} + ${d.videos || 0}`,
          imp ? `${imp.imported} imported${imp.importing ? `, ${imp.importing} importing` : imp.waiting ? `, ${imp.waiting} new` : ''}` : '—'),
        stat('Space', d.total ? `${bytes(d.used)} of ${bytes(d.total)}` : '—', [d.fstype, used != null ? `${used}% full` : null].filter(Boolean).join(' · '))),
      d.total ? h('div.drv-bar', h('span', { style: `width:${used}%` })) : null,
      imp?.waiting && !imp.importing && !d.scanning ? h('div',
        button(`Import ${imp.waiting} new ${imp.waiting === 1 ? 'picture or video' : 'pictures and videos'}`,
          { variant: 'primary', onclick: () => act('import', { id: d.id }, 'Importing. They show up in the Media library as they\'re ready.') }, 'image')) : null,
      songSkips.length || mediaSkips.length ? h('details.drv-skips',
        h('summary', `${songSkips.length + mediaSkips.length} not added`),
        h('ul', [...songSkips.map(s => skipRow(d, s, false)), ...mediaSkips.map(s => skipRow(d, s, true))])) : null));
}

function stat(label, value, sub) {
  return h('div.drv-stat', h('div.stat-label', label), h('div.stat-value', value), h('div.stat-sub', sub));
}

function skipRow(d, s, media) {
  return h('li', h('span.drv-path', s.path), h('span.muted', ` — ${SKIP_REASON[s.reason] || s.reason}`),
    media ? button('Import again', { variant: 'small', onclick: () => act('reimport', { id: d.id, path: s.path }, 'Importing…') }) : null);
}

function historyRow(d) {
  const when = d.last_seen ? new Date(d.last_seen * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '';
  return h('div.ext-row',
    h('div.ext-main', h('div.ext-name', d.label || 'USB drive'),
      h('div.ext-meta', `${d.song_count} songs · ${d.plays} plays · last plugged in ${when}`)),
    h('div.ext-side', button('Forget', {
      variant: 'danger',
      onclick: async () => {
        const ok = await confirmDialog(`Forget ${d.label || 'this drive'}?`,
          'Its songs\' play counts and your edits to their info are removed from the jukebox. Plugged in again, it starts fresh.',
          { confirm: 'Forget', danger: true });
        if (ok) act('forget', { id: d.id }, 'Forgotten.');
      },
    }, 'trash-2')));
}

function importCard() {
  const s = data.settings || { cap_mb: 2048 };
  const cap = h('input', { type: 'number', min: 0, step: 256, value: s.cap_mb });
  return h('div.card',
    h('div.card-head', h('h2', 'Pictures and videos')),
    h('div.card-pad.stack',
      h('p.muted', 'When a drive has new pictures or videos, the jukebox asks before copying them into the Media library (on its screen, or with Import above), made right for the screen. Each is copied once: one you delete there doesn\'t come back.'),
      field('Media library limit (MB)', cap, 'Importing stops when the Media library reaches this size. Music is never copied: it plays from the drive.'),
      h('div', button('Save', { variant: 'primary', onclick: () => act('settings', { cap_mb: Number(cap.value) }, 'Saved.') }, 'check'))));
}

// -- Songs -----------------------------------------------------------------

const allSongs = () => plugged().flatMap(d => (d.songs || []).map(s => ({ ...s, drive: d.label || 'USB drive' })));
const needsInfo = s => !s.artist || !s.album;

function songRow(s) {
  return h('div.drv-song',
    s.cover ? h('img.drv-cover', { src: s.cover, alt: '', loading: 'lazy' }) : h('div.drv-cover.blank', icon('music')),
    h('div.drv-song-text',
      h('div.ext-name', s.title, s.edited ? h('span.drv-badge', 'edited') : null, needsInfo(s) ? h('span.drv-badge.warn', 'needs info') : null),
      h('div.ext-meta', [s.artist || 'No artist', s.album || 'No album', s.album_track ? `#${s.album_track}` : null].filter(Boolean).join(' · ')),
      h('div.ext-meta.drv-path', s.path)),
    button('Edit', { variant: 'small', onclick: () => editSong(s) }, 'pencil'));
}

function newSongs() {
  const songs = allSongs().filter(s => s.first_seen).sort((a, b) => b.first_seen - a.first_seen || a.title.localeCompare(b.title));
  if (!songs.length) return emptyState('sparkles', 'Nothing new', 'Songs the jukebox finds on a drive show up here first, newest at the top.');
  const groups = [];
  for (const s of songs.slice(0, NEW_LIMIT)) {
    const day = new Date(s.first_seen * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    const g = groups.at(-1);
    if (g && Math.abs(g.at - s.first_seen) < 120) g.songs.push(s);
    else groups.push({ at: s.first_seen, day, songs: [s] });
  }
  const missing = songs.filter(needsInfo).length;
  return h('div.stack',
    missing ? h('div.notice.warn', icon('circle-alert'), h('span', `${missing} new song${missing === 1 ? '' : 's'} with no artist or album. Fill it in with Edit: it\'s what the screen shows and how covers are found.`)) : null,
    ...groups.map(g => h('div.card',
      h('div.card-head', h('h2', `Added ${g.day}`), h('span.muted', `${g.songs.length} song${g.songs.length === 1 ? '' : 's'}`)),
      h('div.drv-songs', g.songs.map(songRow)))));
}

let songsList = null;

function renderSongs() {
  const input = h('input', { type: 'search', placeholder: 'Find a song, artist or album', value: search,
    oninput: () => { search = input.value; fillSongs(); } });
  songsList = h('div.drv-songs');
  clear(body,
    h('div.drv-filter', input, switchControl(needsOnly, v => { needsOnly = v; fillSongs(); }, 'Only songs that need info')),
    h('div.card', songsList));
  fillSongs();
}

function fillSongs() {
  const q = search.trim().toLowerCase();
  const songs = allSongs()
    .filter(s => !needsOnly || needsInfo(s))
    .filter(s => !q || [s.title, s.artist, s.album, s.path].some(v => String(v || '').toLowerCase().includes(q)))
    .sort((a, b) => a.title.localeCompare(b.title));
  clear(songsList, songs.length ? songs.map(songRow)
    : emptyState('music', plugged().length ? 'No songs match' : 'No drive plugged in', null));
}

// -- Editing ---------------------------------------------------------------

function editSong(s) {
  const tags = s.tags || {};
  const inputs = {};
  const input = (key, label, type = 'text') => {
    const value = s[key] ?? '';
    const tag = tags[key];
    inputs[key] = h('input', { type, value: String(value), placeholder: tag ? `From the file: ${tag}` : 'Not in the file', min: type === 'number' ? 1 : undefined });
    return field(label, inputs[key]);
  };
  // Only what differs from the file's own tag is kept as an edit.
  const values = () => Object.fromEntries(Object.entries(inputs).map(([k, el]) => {
    const v = el.value.trim();
    return [k, v === String(tags[k] ?? '') ? '' : v];
  }));
  modal({
    title: 'Song info',
    body: h('div.stack',
      h('p.muted', `${s.path} on ${s.drive}. Changes are kept on the jukebox; the file on the drive isn't touched.`),
      input('title', 'Title'), input('artist', 'Artist'), input('album', 'Album'),
      h('div.drv-two', input('album_track', 'Track #', 'number'), input('genre', 'Genre'))),
    actions: [
      s.edited ? { label: 'Use the file\'s info', onclick: () => act('edit', { item_path: s.item_path, fields: {} }, 'Back to the file\'s info.') } : null,
      { label: 'Cancel' },
      { label: 'Save', variant: 'primary', icon: 'check', onclick: () => act('edit', { item_path: s.item_path, fields: values() }, 'Saved. The screen updates in a moment.') },
    ].filter(Boolean),
  });
}
