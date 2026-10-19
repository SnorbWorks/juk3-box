// Sound: whole-home audio. The jukebox plays in the cabinet and on an
// AirPlay speaker (a JUKE Audio zone) at the same time, in sync; jb-audio
// does the work (services/jb_audio/outputs.py).

import { get, post } from '../api.js';
import { button, clear, field, h, icon, pageHeader, showError, switchControl, toast } from '../ui.js';

let timer = null;
let body = null;
let editing = false;   // don't redraw under a slider being dragged

export async function show(main) {
  body = h('div.stack');
  clear(main, pageHeader('Sound', 'Play in the house too: the cabinet and an AirPlay speaker at the same time, in sync.'), body);
  await refresh();
  timer = setInterval(refresh, 2000);
}

export function leave() {
  clearInterval(timer);
  timer = null;
}

async function refresh() {
  if (editing) return;
  let data;
  try {
    data = await get('/api/audio');
  } catch (e) {
    clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  if (body?.isConnected) render(data);
}

async function act(action, payload, done) {
  try {
    await post(`/api/audio/${action}`, payload);
    if (done) toast(done, 'ok');
    setTimeout(refresh, 700);
  } catch (e) {
    showError(e);
  }
}

function statusLine(w) {
  if (!w.enabled) return 'Off: only the cabinet plays.';
  if (!w.airplay_found) return `Looking for ${w.airplay}… Is it on, and on the same Wi-Fi?`;
  if (w.streaming) return `Playing on ${w.airplay} too.`;
  return `Ready. ${w.airplay} joins in when music plays and is let go 30 seconds after it stops.`;
}

function powerCard(w, receivers) {
  const toggle = switchControl(w.enabled, value => act('whole_home', { enabled: value, airplay: w.airplay },
    value ? `Whole-home is on: ${w.airplay}.` : 'Whole-home is off.'), w.enabled ? 'On' : 'Off');
  const names = receivers.includes(w.airplay) ? receivers : [w.airplay, ...receivers];
  const pick = h('select', {
    onfocus: () => { editing = true; },
    onblur: () => { editing = false; },
    onchange: () => {
      editing = false;
      act('whole_home', { enabled: w.enabled, airplay: pick.value }, `AirPlay speaker: ${pick.value}.`);
    },
  }, names.map(n => h('option', { value: n, selected: n === w.airplay }, n)));
  return h('div.card',
    h('div.card-pad.phone-row',
      h('div.stat-icon', icon('house')),
      h('div.phone-text', h('div.stat-value', 'Play in the house too'), h('div.stat-sub', statusLine(w))),
      toggle),
    h('div.card-pad.stack',
      field('AirPlay speaker', pick, 'The speakers it plays on as well. Changing it takes a few seconds.')));
}

function slider({ min, max, step, value, format, onchange }) {
  const out = h('span.muted', format(value));
  const input = h('input', {
    type: 'range', min, max, step, value, style: { flex: '1', minWidth: '0' },
    oninput: () => { editing = true; out.textContent = format(Number(input.value)); },
    onchange: () => { editing = false; onchange(Number(input.value)); },
  });
  return h('div', { style: { display: 'flex', gap: '12px', alignItems: 'center' } }, input, out);
}

function tuningCard(w) {
  const nudge = ms => act('whole_home_delay', { ms: Math.max(0, Math.min(4000, w.delay_ms + ms)) });
  return h('div.card', h('div.card-head', h('h2', 'Fine-tuning')), h('div.card-pad.stack',
    field('AirPlay level', slider({
      min: 0, max: 100, step: 5, value: Math.round(w.airplay_volume * 100), format: v => `${v}%`,
      onchange: v => act('airplay_volume', { value: v / 100 }, `AirPlay level ${v}%.`),
    }), 'How loud the AirPlay speaker plays. The cabinet knob sets the cabinet; the zone\'s own volume in JUKE Audio still applies.'),
    field('Sync', h('div.quick',
      button('Cabinet is early', { onclick: () => nudge(20) }, 'chevron-left'),
      h('span.muted', `${w.delay_ms} ms`),
      button('Cabinet is late', { onclick: () => nudge(-20) }, 'chevron-right')),
      'Stand where you hear both. If the cabinet plays first, it\'s early: hold it back. Each tap is 20 ms.')));
}

function render(data) {
  const w = data.whole_home;
  clear(body,
    data.bus ? null : h('div.notice.warn', icon('circle-alert'),
      h('span', 'The admin can\'t reach the jukebox right now, so this may be out of date.')),
    !w ? h('div.notice.warn', icon('circle-alert'), h('span', 'Waiting to hear from the jukebox\'s sound service…'))
      : [powerCard(w, data.receivers), w.enabled ? tuningCard(w) : null]);
}
