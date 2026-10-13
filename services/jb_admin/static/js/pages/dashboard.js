// Dashboard: what the jukebox is doing right now, and shortcuts.

import { get } from '../api.js';
import { button, clear, h, icon, pageHeader } from '../ui.js';

let timer = null;

export async function show(main) {
  const body = h('div.stack');
  clear(main, pageHeader('Dashboard', 'Your jukebox at a glance.'), body);
  const refresh = async () => {
    let s;
    try {
      s = await get('/api/status');
    } catch (e) {
      clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
      return;
    }
    render(body, s);
  };
  await refresh();
  timer = setInterval(refresh, 4000);
}

export function leave() {
  clearInterval(timer);
}

function stat(iconName, label, value, sub) {
  return h('div.card.card-pad.stat',
    h('div.stat-icon', icon(iconName)),
    h('div', h('div.stat-label', label), h('div.stat-value', value), sub ? h('div.stat-sub', sub) : null));
}

function render(body, s) {
  const np = s.live.now_playing;
  const playing = np?.title && np.status && np.status !== 'stopped';
  const phone = s.live.phone;
  const vol = s.live.volume;
  const declared = s.modules.filter(m => !m.undeclared);
  const down = declared.filter(m => m.up === false);
  const up = declared.filter(m => m.up === true);

  clear(body,
    s.bus ? null : h('div.notice.warn', icon('circle-alert'),
      h('span', 'The admin can\'t reach the jukebox\'s message bus, so live status is missing.')),
    h('div.grid.cols-3',
      stat(playing && np.status === 'playing' ? 'play' : 'pause', 'Now playing',
        playing ? np.title : 'Nothing', playing ? [np.artist, np.album].filter(Boolean).join(' · ') : null),
      stat('bluetooth', 'Phone', phone?.connected ? phone.name : 'Not connected',
        phone?.connected ? 'Connected over Bluetooth' : 'Connect from the phone\'s Bluetooth settings'),
      stat('volume-2', 'Volume', typeof vol?.volume === 'number' ? `${Math.round(vol.volume * 100)}%` : '—',
        vol?.muted ? 'Muted' : null),
      stat('palette', 'Theme', s.theme, h('a', { href: '#/themes' }, 'Change theme')),
      stat('activity', 'Modules', `${up.length} of ${declared.length} running`,
        down.length ? `Down: ${down.map(m => m.name).join(', ')}` : 'All good')),
    h('div.card',
      h('div.card-head', h('h2', 'Get started')),
      h('div.card-pad.quick',
        button('Edit the Home screen', { variant: 'primary', onclick: () => { location.hash = '#/builder/home'; } }, 'layout-template'),
        button('Edit the frame', { onclick: () => { location.hash = '#/builder/frame'; } }, 'panel-left'),
        button('Change theme', { onclick: () => { location.hash = '#/themes'; } }, 'palette'),
        button('Upload pictures', { onclick: () => { location.hash = '#/media'; } }, 'image'),
        button('Settings', { onclick: () => { location.hash = '#/settings'; } }, 'settings'))),
    down.length ? h('div.card',
      h('div.card-head', h('h2', 'Needs attention')),
      h('ul.list', down.map(m => h('li', h('span.dot.down'), h('strong', m.name), h('span.muted', m.id))))) : null);
}
