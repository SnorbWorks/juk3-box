// Featured Artist: the engine picks an artist at random, at startup and
// every half hour, so the Home screen changes on its own.

import { store } from './store.js';

const EVERY_MS = 30 * 60 * 1000;

function pick() {
  const tracks = store.get('tracks') || [];
  // Artists with a cover to show, so the card always has a picture.
  const withArt = new Map();
  for (const t of tracks) {
    if (t.artist && t.artwork_url && !withArt.has(t.artist)) withArt.set(t.artist, t.artwork_url);
  }
  const names = [...withArt.keys()];
  if (!names.length) return;
  const current = store.get('featured')?.artist;
  const choices = names.length > 1 ? names.filter(n => n !== current) : names;
  const artist = choices[Math.floor(Math.random() * choices.length)];
  store.set('featured', { artist, artwork_url: withArt.get(artist) });
}

export function startFeatured() {
  // First pick as soon as the library arrives; again if the featured
  // artist leaves the library.
  store.watch(document.documentElement, 'tracks', tracks => {
    const f = store.get('featured');
    if (tracks?.length && (!f || !tracks.some(t => t.artist === f.artist))) pick();
  });
  setInterval(pick, EVERY_MS);
}
