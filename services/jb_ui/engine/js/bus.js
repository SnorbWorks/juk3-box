// The engine's link to the MQTT bus. Messages land in the store; blocks
// read the store, never the bus.

import { click, rotate } from './focus.js';
import { store } from './store.js';
import { libraryIndex } from './tracks.js';

let client = null;

// The admin's preview runs this same engine in a browser elsewhere on the
// network (index.html?preview). It shows the live jukebox but must never
// act on it: a keypad pick seen by both the kiosk and a preview would be
// picked twice. So a preview publishes nothing.
export const PREVIEW = new URLSearchParams(location.search).has('preview');

// Only what the engine uses. Not jukebox/#: that would also pull in large
// payloads it ignores (e.g. the retained crawl-result).
const HANDLERS = {
  'jukebox/jb-source-phone/now-playing': np => nowPlayingFrom('phone', np),
  'jukebox/jb-source-local/now-playing': np => nowPlayingFrom('local', np),
  'jukebox/jb-source-helper/now-playing': np => nowPlayingFrom('helper', np),
  'jukebox/jb-source-helper/pairing': p => store.set('helperPairing', p),
  'jukebox/jb-source-local/event': e => store.set('driveEvent', { ...e, at: Date.now() }),
  'jukebox/jb-admin/drive-offers': o => store.set('driveOffers', o.offers || []),
  'jukebox/jb-source-phone/connected': c => store.set('phone', c),
  // Which phone has the jukebox, Bluetooth or the app (jb-core's phone_choice.py),
  // and a phone that arrived meanwhile (offered); the app's phones; drives in.
  'jukebox/jb-core/source': s => store.set('source', s),
  'jukebox/jb-source-helper/connected': h => store.set('helper', h),
  'jukebox/jb-source-helper/settings': s => store.set('helperSettings', s),
  // New covers from the app landed: tiles drawn before them load them now.
  'jukebox/jb-source-helper/artwork': a => store.set('coversAt', a.at),
  'jukebox/jb-library/drives-in': d => store.set('drivesIn', d.drives || []),
  'jukebox/jb-source-phone/sync': s => store.set('phoneSync', s),
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
  'jukebox/jb-core/power': p => store.set('power', p),
  'jukebox/jb-source-phone/phones': p => store.set('phones', p.phones || []),
  'jukebox/jb-source-phone/pairing': s => store.set('pairing', s),
  'jukebox/jb-source-phone/switch': s => store.set('phoneSwitch', s),
  'jukebox/jb-source-phone/bluetooth': b => store.set('bluetooth', b),
  'jukebox/jb-audio/airplay': a => store.set('airplay', a.receivers || []),
  'jukebox/jb-display/state': d => store.set('display', d),
  'jukebox/jb-core/pick-problem': p => store.set('pickProblem', { ...p, at: Date.now() }),
  'jukebox/jb-input/gesture': knob,
  // The owner published a change in the admin (settings, layouts, theme):
  // start again with it. A preview is reloaded by the admin instead.
  // A module's own settings (what: ["module:<id>"]) are that module's
  // business: no reload for those.
  'jukebox/jb-admin/changed': p => {
    const what = Array.isArray(p?.what) ? p.what : [];
    if (what.length && what.every(w => String(w).startsWith('module:'))) return;
    if (!PREVIEW) location.reload();
  },
};

// Three sources report now-playing: the phone, USB drives (jb-source-local)
// and the juk3box Helper app (jb-source-helper). Only one plays at a time
// (jb-core pauses or stops the others), so the screen shows
// the one playing -- or, when neither is, the last one that was.
let npSource = 'phone';
function nowPlayingFrom(source, np) {
  if (np?.status === 'playing') npSource = source;
  if (source === npSource) store.set('nowPlaying', np);
}

// The knob arrives from jb-input, which grabs the keypad exclusively, so
// navigation works whether or not the kiosk window has keyboard focus.
// A turn carries signed steps, clockwise positive.
function knob(g) {
  if (g.control !== 'knob') return;
  activity();
  if (asleepSwallow()) return;
  if (g.gesture === 'turn' && Number.isInteger(g.steps)) rotate(g.steps);
  else if (g.gesture === 'click') click();
}

// The screen is asleep (jb-display): the first turn or click only wakes it
// (jb-display hears it too and turns the screen on), so nothing is picked
// in the dark. Once per sleep: if jb-display has gone away its retained
// state may still say asleep, and the knob must not stay dead.
let swallowed = false;
function asleepSwallow() {
  if (!store.get('display')?.asleep) {
    swallowed = false;
    return false;
  }
  if (swallowed) return false;
  swallowed = true;
  return true;
}

// Someone is using the jukebox (knob or keypad).
function activity() {
  store.set('activity', Date.now());
}

// Topics blocks from modules show (installed-modules.md): each message is kept
// in the store as "topic:<topic>".
const extra = new Set();
export function watchTopic(topic) {
  if (extra.has(topic) || HANDLERS[topic]) return;
  extra.add(topic);
  if (client?.connected) client.subscribe(topic);
}

export function publish(topic, payload) {
  if (PREVIEW) return;
  client?.publish(topic, JSON.stringify(payload));
}

export function connectBus() {
  // timerVariant 'native': the default keepalive timers break when NTP
  // jumps the Pi's clock forward at boot (no RTC), and the connection then
  // drops every few seconds forever. See the 2026-09-29 fix (6d1ccaf).
  // The bus is on the same machine as the page: the kiosk loads it from
  // 127.0.0.1, the admin preview from the jukebox's network name.
  client = mqtt.connect(`ws://${location.hostname || 'localhost'}:9001`, { timerVariant: 'native' });
  client.on('connect', () => {
    store.set('bus', 'connected');
    client.subscribe([...Object.keys(HANDLERS), ...extra]);
  });
  client.on('close', () => store.set('bus', 'disconnected'));
  client.on('message', (topic, payload) => {
    // mqtt.js calls this from inside its stream parser; an exception
    // escaping here can tear down the connection. One bad message should
    // cost that message only.
    try {
      if (HANDLERS[topic]) HANDLERS[topic](JSON.parse(payload.toString()));
      else if (extra.has(topic)) store.set(`topic:${topic}`, payload.length ? JSON.parse(payload.toString()) : null);
    } catch (e) {
      console.error('bad message on', topic, e);
    }
  });
}
