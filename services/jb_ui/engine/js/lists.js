// Playlists that modules provide (docs/contracts/extensions.md, playlist
// blocks): the module publishes {hook, subtitle, icon, songs: [{title,
// artist}], autoplay_after} on its topic; songs are matched to the library,
// so only songs the jukebox can play are shown or queued. Opened on the
// song board, played as one pick (shuffled), and, when the module asks
// for it, started on its own once the jukebox has been quiet a while.

import { PREVIEW, publish, watchTopic } from './bus.js';
import { store } from './store.js';

const lists = {};   // block name -> its definition (topic, ...)

export function registerList(name, def) {
  if (!lists[name]) {
    lists[name] = def;
    watchTopic(def.topic);
  }
}

export function isList(name, ctx) {
  return ctx.moduleBlocks?.[name]?.kind === 'playlist';
}

export function listData(name) {
  const def = lists[name];
  return def ? store.get(`topic:${def.topic}`) || null : null;
}

// The list's songs, as library tracks, in the module's order.
export function listSongs(name, tracks = store.get('tracks') || []) {
  const data = listData(name);
  if (!Array.isArray(data?.songs)) return [];
  const exact = new Map();
  const byTitle = new Map();
  for (const t of tracks) {
    const title = (t.title || '').toLowerCase();
    exact.set(`${title}\n${(t.artist || '').toLowerCase()}`, exact.get(`${title}\n${(t.artist || '').toLowerCase()}`) || t);
    if (!byTitle.has(title)) byTitle.set(title, t);
  }
  const seen = new Set();
  const out = [];
  for (const s of data.songs) {
    const title = String(s?.title || '').toLowerCase();
    const t = exact.get(`${title}\n${String(s?.artist || '').toLowerCase()}`) || byTitle.get(title);
    if (t && !seen.has(t)) {
      seen.add(t);
      out.push(t);
    }
  }
  return out;
}

// The whole list as one pick, shuffled.
export function playList(name) {
  const songs = listSongs(name);
  if (!songs.length) return false;
  for (let i = songs.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [songs[i], songs[j]] = [songs[j], songs[i]];
  }
  publish('jukebox/jb-core/cmd', { action: 'select_album', album: listData(name)?.hook || name, tracks: songs });
  return true;
}

// Autoplay: a list whose module asks for it (autoplay_after, minutes) plays
// once the jukebox has been quiet that long -- nothing playing, nothing
// queued, nobody at the keypad, a phone connected. Once per quiet spell:
// it waits for music to have played (and stopped) again before the next.
export function startAutoplay(ctx) {
  if (PREVIEW) return;
  for (const [name, def] of Object.entries(ctx.moduleBlocks || {})) {
    if (def.kind === 'playlist') registerList(name, def);
  }
  let quietSince = Date.now();
  let firedThisQuiet = false;
  setInterval(() => {
    const np = store.get('nowPlaying');
    const playing = np?.status === 'playing';
    const queued = (store.get('queue')?.upcoming || []).length > 0;
    if (playing || queued) {
      quietSince = Date.now();
      firedThisQuiet = false;
      return;
    }
    if (firedThisQuiet || !store.get('phone')?.connected) return;
    const idleFor = Date.now() - Math.max(quietSince, store.get('activity') || 0);
    for (const name of Object.keys(lists)) {
      const after = Number(listData(name)?.autoplay_after) || 0;
      if (after > 0 && idleFor >= after * 60000 && playList(name)) {
        firedThisQuiet = true;
        return;
      }
    }
  }, 30000);
}
