// Light Show: what the cabinet's lights play (docs/contracts/lights.md).
// Now (what's on), Shows (the library, rotations per genre, the idle look,
// a pin, uploads), Preview (the engine plays a show to a demo song, drawn on
// a picture of the cabinet), Inputs (modules' lights), Safety (brightness,
// low-flash, quiet hours) and Hardware (what's wired). jb-lights picks up
// every change within a couple of seconds.

import { del, get, post, upload } from '../api.js';
import { button, clear, confirmDialog, emptyState, field, h, icon, modal, pageHeader, showError, switchControl, toast } from '../ui.js';

const TABS = [['now', 'Now'], ['shows', 'Shows'], ['preview', 'Preview'], ['inputs', 'Inputs'], ['safety', 'Safety'], ['hardware', 'Hardware']];
const LIVE_TABS = new Set(['now', 'inputs']);
const LOOKS = {
  idle: 'Resting: the city at night', ribbon: 'Resting: the waves', 'idle-show': 'Resting: your idle show',
  show: 'The light show, following the music', eq: 'The graphic EQ',
};
const TIERS = { calm: 'calm', main: 'main', peak: 'peak (a drop or a big chorus)' };
const ROLES = { storyteller: 'Storyteller', rhythm: 'Rhythm', meter: 'Meter', keys: 'Keys' };
const SOURCES = { builtin: 'Built-in', uploaded: 'Uploaded' };

let tab = 'now';
let data = null;
let body = null;
let tabs = null;
let timer = null;
let editing = false;      // don't redraw under a slider being dragged
let previewId = null;
let player = null;

export async function show(main) {
  body = h('div.stack');
  tabs = h('div.segmented', TABS.map(([id, label]) =>
    h('button', { type: 'button', dataset: { tab: id }, onclick: () => go(id) }, label)));
  clear(main, pageHeader('Light Show', 'What the cabinet\'s lights play: the shows, which songs get which, what modules add, and the limits nothing gets past.'),
    h('div.ls-tabs', tabs), body);
  await refresh(true);
  timer = setInterval(() => { if (LIVE_TABS.has(tab)) refresh(); }, 3000);
}

export function leave() {
  clearInterval(timer);
  timer = null;
  player?.stop();
  player = null;
}

function go(id) {
  tab = id;
  if (id !== 'preview') { player?.stop(); player = null; }
  render();
}

async function refresh(force) {
  if (editing && !force) return;
  try {
    data = await get('/api/lights');
  } catch (e) {
    clear(body, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  if (body?.isConnected && (force || tab !== 'preview')) render();
}

async function save(change, done) {
  try {
    const r = await post('/api/lights/settings', change);
    data.settings = r.settings;
    if (done) toast(done, 'ok');
    await refresh(true);
  } catch (e) {
    showError(e);
  }
}

function render() {
  for (const b of tabs.children) b.setAttribute('aria-pressed', String(b.dataset.tab === tab));
  const pane = { now: nowPane, shows: showsPane, preview: previewPane, inputs: inputsPane, safety: safetyPane, hardware: hardwarePane }[tab];
  clear(body,
    data.bus ? null : h('div.notice.warn', icon('circle-alert'), h('span', 'The admin can\'t reach the jukebox right now, so this may be out of date.')),
    pane());
}

const showName = id => data.shows.find(s => s.id === id)?.name || id;
const groupName = id => data.groups.find(g => g.id === id)?.name || id;

// Now -------------------------------------------------------------------------

function nowPane() {
  const now = data.now;
  if (!now) return h('div.notice.warn', icon('circle-alert'), h('span', 'Waiting to hear from the lights… (jb-lights reports every couple of seconds.)'));
  const live = Object.entries(now.inputs || {}).filter(([, v]) => v.signal || v.overlays?.length || (v.last_s != null && v.last_s < 60));
  const s = data.settings;
  return [
    h('div.card', h('div.card-pad.phone-row',
      h('div.stat-icon', icon('lightbulb')),
      h('div.phone-text',
        h('div.stat-value', now.show ? `${now.show.name}${now.tier ? ` · ${TIERS[now.tier] || now.tier}` : ''}` : LOOKS[now.look] || now.look),
        h('div.stat-sub', now.show ? LOOKS[now.look] || now.look : `When music plays, the light show takes over. ${s.pin ? `Every song gets ${showName(s.pin)} (pinned).` : 'Each song gets a show for its genre.'}`)))),
    h('div.card', h('div.card-head', h('h2', 'Modules lighting it')), h('div.card-pad.stack',
      live.length ? live.map(([id, v]) => h('div', h('strong', id), ' ',
        h('span.muted', [v.signal ? 'colours' : null, v.overlays?.length ? `${v.overlays.length} overlay${v.overlays.length > 1 ? 's' : ''}` : null,
          v.last_s != null ? `last heard ${v.last_s} s ago` : null].filter(Boolean).join(' · '))))
        : h('p.muted', 'None right now.'))),
    data.notes.length ? h('div.card', h('div.card-head', h('h2', 'Shows that couldn\'t be used')),
      h('div.card-pad', h('ul.ls-notes', data.notes.map(n => h('li', n))))) : null,
    h('p.field-help', `Lights wired in: ${(now.chains || []).join(', ') || 'none'}. ${s.brightness < 1 ? `Brightness ${Math.round(s.brightness * 100)}%.` : ''}${s.low_flash ? ' Low-flash is on.' : ''}`),
  ];
}

// Shows -----------------------------------------------------------------------

function swatches(colours) {
  return h('div.ls-swatches', colours.map(c => h('span', { style: { background: c } })));
}

function showsPane() {
  const s = data.settings;
  const input = h('input', { type: 'file', accept: '.json,application/json', hidden: true,
    onchange: () => { const f = input.files[0]; input.value = ''; if (f) uploadShow(f); } });
  const choices = (none, label) => [h('option', { value: none }, label),
    ...data.shows.filter(x => !x.disabled).map(x => h('option', { value: x.id }, x.name))];
  const pin = h('select', { onchange: () => save({ pin: pin.value || null }, pin.value ? `Every song gets ${showName(pin.value)}.` : 'Songs get a show for their genre again.') },
    choices('', 'No: a show for each song\'s genre'));
  pin.value = s.pin || '';
  const idle = h('select', { onchange: () => save({ idle: idle.value }, 'Idle look saved.') }, choices('city', 'The city at night and the waves'));
  idle.value = s.idle;
  return [
    h('div.card', h('div.card-pad.stack',
      h('div.grid.cols-2',
        field('With no music', idle, 'What the cabinet does while nothing plays.'),
        field('Pin a show', pin, 'One show for every song, whatever its genre.')),
      h('div.quick', button('Upload a show', { onclick: () => input.click() }, 'upload'), input,
        h('span.field-help', 'A show file (.json). Download one below to start from.')))),
    h('div.card', h('div.card-head', h('h2', 'Which songs get which shows')), h('div.card-pad.stack',
      h('p.field-help', 'Each genre has shows that take turns, song by song. The same song always gets the same one.'),
      data.groups.map(g => h('div.ls-rot',
        h('div.ls-rot-name', g.name, g.custom ? h('span.badge.accent', 'Yours') : null),
        h('div.chips', g.rotation.map(id => h('span.chip', showName(id)))),
        button('Change', { variant: 'small', onclick: () => editRotation(g) }, 'pencil'))))),
    h('div.grid.cols-2', data.shows.map(showCard)),
  ];
}

function showCard(x) {
  const s = data.settings;
  const source = SOURCES[x.source] || x.source;
  return h('div.card.ls-show', h('div.card-pad.stack',
    h('div.ls-show-head', h('strong', x.name),
      h('span.badge', source), s.pin === x.id ? h('span.badge.accent', 'Pinned') : null,
      s.idle === x.id ? h('span.badge.accent', 'Idle look') : null, x.disabled ? h('span.badge.warn', 'Off') : null),
    swatches(x.palette),
    x.description ? h('p.muted', x.description) : null,
    x.for.length ? h('p.field-help', `For ${x.for.map(groupName).join(', ').toLowerCase()}.${x.author && x.source !== 'builtin' ? ` By ${x.author}.` : ''}`) : null,
    h('div.quick',
      button('Preview', { variant: 'small', onclick: () => { previewId = x.id; go('preview'); } }, 'play'),
      h('a.btn.small', { href: `/api/lights/shows/${encodeURIComponent(x.id)}`, download: `${x.id}.json` }, icon('download'), 'Download'),
      x.source === 'uploaded' ? button('Delete', { variant: 'small', onclick: () => deleteShow(x) }, 'trash-2') : null,
      switchControl(!x.disabled, on => save({ disabled: on ? s.disabled.filter(i => i !== x.id) : [...s.disabled, x.id] },
        on ? `${x.name} is back in the rotations.` : `${x.name} won't be picked.`), 'In use'))));
}

function editRotation(g) {
  const picked = [...g.rotation];
  const list = h('div.stack');
  const draw = () => clear(list, data.shows.filter(x => !x.disabled).map(x => {
    const n = picked.indexOf(x.id);
    return h('label.ls-pick', h('input', { type: 'checkbox', checked: n >= 0, onchange: e => {
      if (e.target.checked) picked.push(x.id); else picked.splice(picked.indexOf(x.id), 1);
      draw();
    } }), h('span', x.name), n >= 0 ? h('span.chip-n', String(n + 1)) : null,
    x.for.includes(g.id) ? h('span.muted', `made for ${g.name}`) : null);
  }));
  draw();
  const m = modal({ title: `${g.name}: shows that take turns`, body: h('div.stack',
    h('p.field-help', 'Tick the shows in the order you want them. A song keeps its show.'), list), actions: [
    button('Every show made for it', { onclick: () => { m.close(); save({ rotations: { [g.id]: null } }, `${g.name}: every show made for it.`); } }),
    button('Save', { variant: 'primary', onclick: () => {
      if (!picked.length) { toast('Tick at least one show.', 'warn'); return; }
      m.close();
      save({ rotations: { [g.id]: picked } }, `${g.name}: saved.`);
    } }, 'check')] });
}

async function uploadShow(file, replace = false) {
  try {
    const r = await upload(`/api/lights/shows${replace ? '?replace=1' : ''}`, file);
    toast(`${r.name} ${r.replaced ? 'replaced' : 'added'}.`, 'ok');
    await refresh(true);
  } catch (e) {
    if (!replace && /Upload it again to replace/.test(e.message)
        && await confirmDialog('Replace it?', `${e.message.split('.')[0]}. Replace it with this file?`, { confirm: 'Replace' })) {
      return uploadShow(file, true);
    }
    showError(e);
  }
}

async function deleteShow(x) {
  if (!await confirmDialog(`Delete ${x.name}?`, 'It goes from every rotation too.', { confirm: 'Delete', danger: true })) return;
  try {
    await del(`/api/lights/shows/${encodeURIComponent(x.id)}`);
    toast(`${x.name} deleted.`, 'ok');
    await refresh(true);
  } catch (e) {
    showError(e);
  }
}

// Preview ---------------------------------------------------------------------

function previewPane() {
  previewId = previewId && data.shows.some(x => x.id === previewId) ? previewId : data.shows[0]?.id;
  const pick = h('select', { onchange: () => { previewId = pick.value; player?.stop(); player = null; render(); } },
    data.shows.map(x => h('option', { value: x.id }, x.name)));
  pick.value = previewId;
  const stage = h('div.ls-stage', h('div.loading', h('span.spinner'), 'The jukebox is playing it to the demo song…'));
  const controls = h('div.quick');
  const caption = h('div.ls-caption');
  loadPreview(previewId, stage, controls, caption);
  return h('div.card', h('div.card-pad.stack',
    field('Show', pick, 'Played by the jukebox\'s own light code to a demo song: an intro, a verse, a build, the drop, a big chorus and an outro.'),
    stage, caption, controls));
}

async function loadPreview(id, stage, controls, caption) {
  let p;
  try {
    p = await get(`/api/lights/preview/${encodeURIComponent(id)}`);
  } catch (e) {
    clear(stage, h('div.notice.danger', icon('circle-alert'), h('span', e.message)));
    return;
  }
  if (!stage.isConnected || id !== previewId) return;
  player?.stop();
  player = makePlayer(p, stage, controls, caption);
}

function makePlayer(p, stage, controls, caption) {
  const raw = Uint8Array.from(atob(p.frames), c => c.charCodeAt(0));
  const perFrame = p.chains.reduce((n, c) => n + c.pixels, 0) * 3;
  const frames = Math.floor(raw.length / perFrame);
  const canvas = h('canvas.ls-canvas');
  clear(stage, canvas);
  const W = 360;
  const unit = W / p.aspect;                       // one picture unit down the cabinet, in px
  const pts = [];
  for (const c of p.chains) {
    if (c.positions) for (const [x, y] of c.positions) pts.push([16 + x * W, 16 + y * unit, c.id === 'accent' ? 2.6 : 2.2]);
  }
  const bottom = Math.max(...pts.map(q => q[1])) + 30;
  for (const [x, row] of p.chains.find(c => c.id === 'buttons')?.keys || []) pts.push([16 + 40 + x * (W - 80), bottom + row * 22, 4]);
  const H = bottom + 3 * 22 + 10;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = (W + 32) * dpr;
  canvas.height = H * dpr;
  canvas.style.aspectRatio = `${W + 32} / ${H}`;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const lift = v => Math.round(255 * Math.min(1, (v / 255) ** 0.6));   // LEDs look brighter than their numbers
  let f = 0;
  let playing = true;
  let last = 0;
  const draw = () => {
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#0b0c0e';
    ctx.fillRect(0, 0, W + 32, H);
    ctx.globalCompositeOperation = 'lighter';
    const base = f * perFrame;
    for (let i = 0; i < pts.length; i++) {
      const r = raw[base + i * 3], g = raw[base + i * 3 + 1], b = raw[base + i * 3 + 2];
      if (r + g + b < 6) continue;
      const [x, y, size] = pts[i];
      ctx.fillStyle = `rgb(${lift(r)},${lift(g)},${lift(b)})`;
      ctx.globalAlpha = 0.25;
      ctx.beginPath(); ctx.arc(x, y, size * 2.4, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
      ctx.beginPath(); ctx.arc(x, y, size, 0, Math.PI * 2); ctx.fill();
    }
    const t = f / p.fps;
    const part = [...p.moments].reverse().find(m => t >= m.t);
    caption.textContent = `${t.toFixed(1)} s · ${part ? part.label : ''} · ${{ c: 'calm', m: 'main', p: 'peak' }[p.tiers[f]] || ''}`;
    bar.value = f;
  };
  let raf = 0;
  const tick = now => {
    raf = requestAnimationFrame(tick);
    if (!playing || now - last < 1000 / p.fps) return;
    last = now;
    draw();
    f = (f + 1) % frames;
  };
  const bar = h('input', { type: 'range', min: 0, max: frames - 1, value: 0, style: { flex: '1', minWidth: '0' },
    oninput: () => { f = Number(bar.value); draw(); } });
  const toggle = () => {
    playing = !playing;
    const b = button(playing ? 'Pause' : 'Play', { onclick: toggle }, playing ? 'pause' : 'play');
    playBtn.replaceWith(b);
    playBtn = b;
  };
  let playBtn = button('Pause', { onclick: toggle }, 'pause');
  clear(controls, playBtn, button('From the start', { onclick: () => { f = 0; draw(); } }, 'rotate-ccw'), bar);
  raf = requestAnimationFrame(tick);
  return { stop: () => cancelAnimationFrame(raf) };
}

// Inputs ----------------------------------------------------------------------

function inputsPane() {
  if (!data.inputs.length) {
    return h('div.card', emptyState('plug', 'No module uses the lights yet',
      'Modules that do (Weather, for one) show up here once they\'re installed, with a switch and a strength for each.'));
  }
  return data.inputs.map(m => {
    const heard = m.heard;
    return h('div.card', h('div.card-pad.stack',
      h('div.phone-row',
        h('div.stat-icon', icon('puzzle')),
        h('div.phone-text', h('div.stat-value', m.name), h('div.stat-sub', heard?.last_s != null ? `Last heard ${heard.last_s} s ago` : 'Nothing heard from it yet')),
        switchControl(m.enabled, on => save({ inputs: { [m.id]: { enabled: on, strength: m.strength } } },
          on ? `${m.name}'s lights are on.` : `${m.name}'s lights are off.`), m.enabled ? 'On' : 'Off')),
      h('ul.ls-notes', m.asks.map(a => h('li', a))),
      m.enabled ? field('Strength', slider({ min: 0, max: 100, step: 5, value: Math.round(m.strength * 100), format: v => `${v}%`,
        onchange: v => save({ inputs: { [m.id]: { enabled: true, strength: v / 100 } } }) }),
      'How much its colours, effects and overlays show.') : null,
      heard?.dropped ? h('div.notice.warn', icon('circle-alert'), h('span', `Its last message was ignored: ${heard.dropped}.`)) : null));
  });
}

// Safety ----------------------------------------------------------------------

function slider({ min, max, step, value, format, onchange }) {
  const out = h('span.muted', format(value));
  const input = h('input', {
    type: 'range', min, max, step, value, style: { flex: '1', minWidth: '0' },
    oninput: () => { editing = true; out.textContent = format(Number(input.value)); },
    onchange: () => { editing = false; onchange(Number(input.value)); },
  });
  return h('div', { style: { display: 'flex', gap: '12px', alignItems: 'center' } }, input, out);
}

function safetyPane() {
  const s = data.settings;
  const q = s.quiet;
  const time = (value, key) => {
    const el = h('input', { type: 'time', value, onchange: () => save({ quiet: { [key]: el.value } }, 'Quiet hours saved.') });
    return el;
  };
  return [
    h('div.card', h('div.card-pad.stack',
      field('Brightness', slider({ min: 10, max: 100, step: 5, value: Math.round(s.brightness * 100), format: v => `${v}%`,
        onchange: v => save({ brightness: v / 100 }, `Brightness ${v}%.`) }), 'On top of every show and every module. The keypad never goes so dim you can\'t read it.'),
      h('div.phone-row', h('div.phone-text', h('div.stat-value', 'Low-flash'),
        h('div.stat-sub', 'White flashes become soft swells, and no light jumps to bright. For anyone sensitive to flashing light.')),
      switchControl(s.low_flash, on => save({ low_flash: on }, on ? 'Low-flash is on.' : 'Low-flash is off.'), s.low_flash ? 'On' : 'Off')),
      h('p.field-help', 'Always, whatever the setting: never more than 3 flashes a second, and each set of lights kept under its current limit.'))),
    h('div.card', h('div.card-head', h('h2', 'Quiet hours')), h('div.card-pad.stack',
      h('div.phone-row', h('div.phone-text', h('div.stat-sub', 'Dimmer at night, every night.')),
        switchControl(q.enabled, on => save({ quiet: { enabled: on } }, on ? 'Quiet hours are on.' : 'Quiet hours are off.'), q.enabled ? 'On' : 'Off')),
      q.enabled ? [
        h('div.grid.cols-2', field('From', time(q.from, 'from')), field('Until', time(q.to, 'to'))),
        field('Brightness then', slider({ min: 5, max: 100, step: 5, value: Math.round(q.brightness * 100), format: v => `${v}%`,
          onchange: v => save({ quiet: { brightness: v / 100 } }, `Quiet hours: ${v}%.`) })),
      ] : null)),
  ];
}

// Hardware --------------------------------------------------------------------

function hardwarePane() {
  const hw = data.hardware;
  return [
    hw.chains.map(c => h('div.card', h('div.card-head', h('h2', c.name), c.wired ? h('span.badge.ok', 'Wired in') : h('span.badge', 'Not wired in')),
      h('div.card-pad.stack',
        h('dl.kv', h('dt', 'Data'), h('dd', `${c.driver.toUpperCase()}${c.gpio != null ? `, GPIO ${c.gpio}` : ''}`),
          h('dt', 'Pixels'), h('dd', `${c.pixels} (${c.order})`), h('dt', 'Current limit'), h('dd', `${c.max_amps} A`)),
        h('ul.ls-notes', c.fixtures.map(fx => h('li', h('strong', fx.name), ` · ${ROLES[fx.role] || fx.role} · ${fx.count} pixels`)))))),
    h('p.field-help', 'Which lights are wired in is set in Setup. A guide for adding your own LED panels and strips comes in a later update.'),
  ];
}
