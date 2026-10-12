// The engine's link to the MQTT bus. Messages land in the store; blocks
// read the store, never the bus.

import { click, rotate } from './focus.js';
import { store } from './store.js';
import { libraryIndex } from './tracks.js';

let client = null;

// Only what the engine uses. Not jukebox/#: that would also pull in large
// payloads it ignores (e.g. the retained crawl-result).
const HANDLERS = {
  'jukebox/jb-source-phone/now-playing': np => store.set('nowPlaying', np),
  'jukebox/jb-source-phone/connected': c => store.set('phone', c),
  'jukebox/jb-core/queue': q => store.set('queue', q),
  'jukebox/jb-library/tracks': lib => {
    store.set('library', libraryIndex(lib.tracks || []));
    store.set('tracks', lib.tracks || []);
  },
  'jukebox/jb-library/playlists': p => store.set('playlists', p.playlists || []),
  'jukebox/jb-library/albums': a => store.set('albums', a),
  'jukebox/jb-crawl/album': s => store.set('albumFetch', s),
  'jukebox/jb-core/entry': e => { activity(); store.set('entry', e); },
  'jukebox/jb-core/selection': s => store.set('selection', s),
  'jukebox/jb-audio/state': v => store.set('volume', v),
  'jukebox/jb-core/notice': n => store.set('notice', n),
  'jukebox/jb-source-phone/play-error': e => store.set('playError', { ...e, at: Date.now() }),
  'jukebox/jb-input/gesture': knob,
};

// The knob arrives from jb-input, which grabs the keypad exclusively, so
// navigation works whether or not the kiosk window has keyboard focus.
// A turn carries signed steps, clockwise positive.
function knob(g) {
  if (g.control !== 'knob') return;
  activity();
  if (g.gesture === 'turn' && Number.isInteger(g.steps)) rotate(g.steps);
  else if (g.gesture === 'click') click();
}

// Someone is using the jukebox (knob or keypad).
function activity() {
  store.set('activity', Date.now());
}

export function publish(topic, payload) {
  client?.publish(topic, JSON.stringify(payload));
}

export function connectBus() {
  // timerVariant 'native': the default keepalive timers break when NTP
  // jumps the Pi's clock forward at boot (no RTC), and the connection then
  // drops every few seconds forever. See the 2026-09-29 fix (6d1ccaf).
  client = mqtt.connect('ws://localhost:9001', { timerVariant: 'native' });
  client.on('connect', () => {
    store.set('bus', 'connected');
    client.subscribe(Object.keys(HANDLERS));
  });
  client.on('close', () => store.set('bus', 'disconnected'));
  client.on('message', (topic, payload) => {
    // mqtt.js calls this from inside its stream parser; an exception
    // escaping here can tear down the connection. One bad message should
    // cost that message only.
    try {
      HANDLERS[topic]?.(JSON.parse(payload.toString()));
    } catch (e) {
      console.error('bad message on', topic, e);
    }
  });
}
