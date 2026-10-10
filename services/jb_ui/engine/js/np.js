// What's playing, for anything that shows it (the Now Playing block and
// the Now Playing overlay). The phone reports its position every few
// seconds; between reports the elapsed time runs on locally.

import { store } from './store.js';
import { formatMs, toTrack } from './tracks.js';

let receivedAt = 0;
store.watch(document.documentElement, 'nowPlaying', () => { receivedAt = Date.now(); });

export function nowPlayingData() {
  const np = store.get('nowPlaying');
  const state = np?.status === 'playing' ? 'playing' : np?.status === 'paused' ? 'paused' : 'stopped';
  const track = np?.title ? toTrack(np, store.get('library')) : {};
  let position = np?.position_ms ?? 0;
  if (state === 'playing') position += Date.now() - receivedAt;
  const duration = np?.duration_ms;
  if (duration) position = Math.min(position, duration);
  return {
    ...track,
    state,
    elapsed: np?.title ? formatMs(position) : '',
    progress: duration ? position / duration : 0,
  };
}

// The library entry for what's playing (for its favorite mark): the copy
// whose artist matches, else the first with that title.
export function playingLibraryTrack() {
  const np = store.get('nowPlaying');
  if (!np?.title) return null;
  const copies = (store.get('tracks') || []).filter(t => t.title === np.title);
  const artist = np.artist?.toLowerCase();
  return copies.find(t => artist && t.artist?.toLowerCase() === artist) || copies[0] || null;
}
