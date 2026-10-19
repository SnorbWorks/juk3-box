// Settings > Whole-home audio (the wholehome block): play on an AirPlay
// speaker (a JUKE Audio zone) as well as the cabinet, in sync. jb-audio does
// the work (services/jb_audio/outputs.py): turning it on measures the
// speaker's delay before switching over; Resync reconnects and measures again.
// The by-ear fine-tuning and the AirPlay level are on the admin's Sound page.
//
// Markup: engine blocks wholehome and popup-speakers (rows are phone-row).

import { bind, fromTemplate, slot } from './bind.js';
import { PREVIEW, publish } from './bus.js';
import { focusKey, makeStop, refocus } from './focus.js';
import { closePopup, openPopup, updatePopup } from './popup.js';
import { store } from './store.js';

const RESULT_MS = 4000;
let templates = {};

const command = (action, extra = {}) => {
  if (!PREVIEW) publish('jukebox/jb-audio/cmd', { action, ...extra });
};
const state = () => store.get('volume')?.whole_home || null;
const speakers = () => store.get('airplay') || [];

export function initWholeHome(blockTemplates) {
  templates = blockTemplates;
}

function note(w) {
  if (!w) return 'Waiting for the sound service…';
  if (!w.enabled) return 'Off: only the cabinet plays.';
  if (!w.airplay_found) return `Looking for ${w.airplay}. Until it's found, only the cabinet plays.`;
  if (w.calibration === 'measuring' || !w.active) return `Measuring ${w.airplay}'s delay…`;
  if (w.streaming) return `Playing on ${w.airplay} too, in sync.`;
  return `${w.airplay} joins in when music plays.`;
}

export function fillWholeHome(el) {
  el.append(fromTemplate(templates.wholehome));
  const actions = { power: togglePower, speaker: pickSpeaker, resync };
  for (const stop of el.querySelectorAll('[data-stop]')) {
    const name = stop.dataset.stop;
    makeStop(stop, `wh:${name}`, () => actions[name]?.());
  }
  const render = () => {
    const w = state();
    bind(el, {
      on: !!w?.enabled,
      powerLabel: w?.enabled ? 'On' : 'Off',
      speaker: w?.airplay || '',
      syncLabel: w?.calibration === 'measuring' ? 'Measuring…' : `${w?.delay_ms ?? ''} ms`,
      note: note(w),
    });
  };
  store.watch(el, 'volume', render);
  render();
}

function result(title, message, back) {
  openPopup('notice', { title, message }, { onClose: () => focusKey(back) });
  setTimeout(() => closePopup('notice'), RESULT_MS);
}

function togglePower() {
  const w = state();
  if (!w) return;
  command('whole_home', { enabled: !w.enabled, airplay: w.airplay });
  if (!w.enabled) result('Whole-home audio', `Connecting to ${w.airplay} and measuring its delay…`, 'wh:power');
}

function resync() {
  const w = state();
  if (!w?.enabled) return;
  command('whole_home_resync');
  result('Whole-home audio', `Reconnecting ${w.airplay} and measuring again…`, 'wh:resync');
}

function pickSpeaker() {
  const w = state();
  if (!w) return;
  const names = speakers().includes(w.airplay) ? speakers() : [w.airplay, ...speakers()];
  const done = () => { closePopup(); focusKey('wh:speaker'); };
  openPopup('speakers', { title: 'AirPlay speaker', empty: !names.length }, { stops: { close: done } });
  const holder = document.querySelector('.jb-popup-frame[data-popup="speakers"] [data-slot="speakers"]');
  holder?.replaceChildren(...names.map((name, i) => {
    const row = fromTemplate(templates['phone-row']).firstElementChild;
    bind(row, { name, status: name === w.airplay ? 'Playing here' : '' });
    row.toggleAttribute('data-connected', name === w.airplay);
    makeStop(row, `popup:speakers:${i}`, () => {
      if (name !== w.airplay) command('whole_home', { enabled: true, airplay: name });
      done();
    });
    return row;
  }));
  updatePopup('speakers', { title: 'AirPlay speaker', empty: !names.length });
  refocus();
  const current = names.indexOf(w.airplay);
  focusKey(`popup:speakers:${Math.max(current, 0)}`);
}
