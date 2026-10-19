// Displays: the cabinet's screen. The picture area pulls each side of the
// picture in so it fits the window in the cabinet's glass; then rotation
// and screen sleep. jb-display does the work (services/jb_display/); the
// cabinet has the same settings under Settings > Displays.

import { get, post } from '../api.js';
import { button, clear, debounce, field, h, icon, iconButton, pageHeader, showError, switchControl, toast } from '../ui.js';

const SIDES = [['top', 'Top edge'], ['bottom', 'Bottom edge'], ['left', 'Left edge'], ['right', 'Right edge']];
const ROTATIONS = [[0, 'Normal'], [90, 'Turned right (90°)'], [180, 'Upside down (180°)'], [270, 'Turned left (270°)']];
const SLEEPS = [[0, 'Never'], [5, 'After 5 minutes'], [10, 'After 10 minutes'], [15, 'After 15 minutes'],
  [30, 'After 30 minutes'], [60, 'After 1 hour'], [120, 'After 2 hours']];

let timer = null;
let body = null;
let editing = false;      // don't redraw under a field being typed in
let guideOn = false;      // the cabinet's guide, if this page turned it on

export async function show(main) {
  body = h('div.stack');
  clear(main, pageHeader('Displays', 'The cabinet\'s screen: fit the picture to the window in the glass, turn it, and let it sleep.'), body);
  await refresh();
  timer = setInterval(refresh, 2000);
}

export function leave() {
  clearInterval(timer);
  timer = null;
  if (guideOn) post('/api/display/guide', { on: false }).catch(() => {});
  guideOn = false;
}

async function refresh() {
  if (editing) return;
  let data;
  try {
    data = await get('/api/display');
  } catch (e) {
    clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  if (body?.isConnected) render(data);
}

async function act(action, payload, done) {
  try {
    await post(`/api/display/${action}`, payload);
    if (done) toast(done, 'ok');
    setTimeout(refresh, 400);
  } catch (e) {
    showError(e);
  }
}

function pictureSize(d) {
  const o = d.output;
  if (!o?.width) return null;
  const [w, h] = d.rotation % 180 ? [o.height, o.width] : [o.width, o.height];
  return [w - d.inset.left - d.inset.right, h - d.inset.top - d.inset.bottom];
}

function edgeControl(d, side, label) {
  const most = d.limits?.[side] ?? 0;
  const send = debounce(() => {
    editing = false;
    const px = Math.max(0, Math.min(most, Math.round(Number(input.value) || 0)));
    input.value = px;
    act('inset', { inset: { [side]: px } });
  }, 400);
  const input = h('input', {
    type: 'number', min: 0, max: most, step: 1, value: d.inset[side], style: { width: '6em' },
    oninput: () => { editing = true; send(); },
    onfocus: () => { editing = true; },
    onblur: () => { editing = false; },
  });
  const nudge = n => () => {
    input.value = Math.max(0, Math.min(most, Number(input.value) + n));
    editing = true;
    send();
  };
  return field(label, h('div.quick',
    iconButton('chevron-left', 'Out 4 px', { onclick: nudge(-4) }),
    input, h('span.muted', 'px'),
    iconButton('chevron-right', 'In 4 px', { onclick: nudge(4) })),
  `Up to ${most} px.`);
}

function areaCard(d) {
  const size = pictureSize(d);
  const pulledIn = Object.values(d.inset).some(v => v > 0);
  const guide = switchControl(!!d.guide, on => {
    guideOn = on;
    act('guide', { on });
  }, 'Show the edge guide on the cabinet');
  return h('div.card', h('div.card-head', h('h2', 'Picture area')), h('div.card-pad.stack',
    h('p.muted', 'Pull each edge in from the edge of the panel until everything on screen sits inside the window in the cabinet\'s glass. The background still fills the whole panel; the rest is fitted inside the edges.'),
    h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' } },
      SIDES.map(([side, label]) => edgeControl(d, side, label))),
    h('p.muted', size ? `The picture is ${size[0]} × ${size[1]}.` : ''),
    h('div.quick', guide,
      pulledIn ? button('Use the whole screen', { onclick: () => act('inset_reset', {}, 'The picture fills the whole screen again.') }, 'maximize-2') : null),
    h('p.field-help', 'The guide draws a line around the picture area on the cabinet, for lining it up from here. It turns itself off after 10 minutes.')));
}

function select(options, value, onchange) {
  const el = h('select', {
    onfocus: () => { editing = true; },
    onblur: () => { editing = false; },
    onchange: () => { editing = false; onchange(Number(el.value)); },
  }, options.map(([v, label]) => h('option', { value: v, selected: v === value }, label)));
  return el;
}

function screenCard(d) {
  const o = d.output;
  return h('div.card', h('div.card-head', h('h2', 'Screen')), h('div.card-pad.stack',
    o ? h('p.muted', `${[o.make, o.model].filter(Boolean).join(' ') || o.name} on ${o.name}: ${o.width} × ${o.height} at ${o.refresh} Hz.`) : null,
    field('Rotation', select(ROTATIONS, d.rotation, degrees => act('rotation', { degrees }, 'Rotation changed.')),
      'For a panel mounted on its side or upside down. The picture area\'s edges stay on the same part of the glass.'),
    field('Screen sleep', select(SLEEPS, d.sleep_minutes, minutes => act('sleep', { minutes }, 'Screen sleep changed.')),
      'The screen turns off when nobody has used the jukebox for this long and nothing is playing. Any button, the keypad or music starting turns it back on.'),
    d.asleep ? h('div.quick', h('span.muted', 'The screen is asleep.'), button('Wake it', { onclick: () => act('wake', {}) }, 'power')) : null));
}

function render(data) {
  const d = data.display;
  clear(body,
    data.bus ? null : h('div.notice.warn', icon('circle-alert'),
      h('span', 'The admin can\'t reach the jukebox right now, so this may be out of date.')),
    !d ? h('div.notice.warn', icon('circle-alert'), h('span', 'Waiting to hear from the jukebox\'s display service…'))
      : [d.sway ? null : h('div.notice.warn', icon('circle-alert'), h('span', 'The display service can\'t reach the screen right now. Is the cabinet\'s screen running?')),
        areaCard(d), screenCard(d)]);
}
