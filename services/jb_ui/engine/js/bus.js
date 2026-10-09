// The engine's link to the MQTT bus. Messages land in the store; blocks
// read the store, never the bus.

import { store } from './store.js';
import { libraryIndex } from './tracks.js';

// Only what the engine uses. Not jukebox/#: that would also pull in large
// payloads it ignores (e.g. the retained crawl-result).
const HANDLERS = {
  'jukebox/jb-source-phone/now-playing': np => store.set('nowPlaying', np),
  'jukebox/jb-source-phone/connected': c => store.set('phone', c),
  'jukebox/jb-core/queue': q => store.set('queue', q),
  'jukebox/jb-library/tracks': lib => store.set('library', libraryIndex(lib.tracks || [])),
};

export function connectBus() {
  // timerVariant 'native': the default keepalive timers break when NTP
  // jumps the Pi's clock forward at boot (no RTC), and the connection then
  // drops every few seconds forever. See the 2026-09-29 fix (6d1ccaf).
  const client = mqtt.connect('ws://localhost:9001', { timerVariant: 'native' });
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
  return client;
}
