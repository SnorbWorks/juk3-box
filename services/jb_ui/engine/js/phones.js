// Phones and Bluetooth on the cabinet (dev plan Phase 4 and the Phase 7
// screens). jb-source-phone does the work; these are its screens.
//
//   The Sources badge (top right): which phone has the jukebox. Clicking it
//   lists every phone to switch to, Bluetooth or the Juk3box Helper app
//   (jb-core chooses: phone_choice.py), and the drives plugged in, plus
//   "Add a phone", which goes to Settings > Bluetooth. A phone arriving
//   while another has the jukebox is offered: "… is here. Switch to it?"
//   Settings > Bluetooth (the bluetooth block): Bluetooth on/off, every
//   paired phone (connect, disconnect, forget) and pairing a new one, with
//   the 6-digit code confirmed here.
//
// The markup is engine blocks a theme can restyle or replace: bluetooth,
// phone-row, popup-phones, popup-phone, popup-confirm, popup-pairing.

import { bind, fromTemplate, slot } from './bind.js';
import { PREVIEW, publish } from './bus.js';
import { currentStopKey, focusKey, makeStop, refocus } from './focus.js';
import { closePopup, openPopup, popupOpen, updatePopup } from './popup.js';
import { store } from './store.js';

const RESULT_MS = 4000;
let templates = {};
let openSettings = () => {};   // shows Settings > Bluetooth (main.js)
let returnTo = null;           // the stop to go back to when a popup closes
let follow = null;             // the open popup's re-render, on new phone data
let switching = false;

const command = (action, extra = {}) => {
  if (!PREVIEW) publish('jukebox/jb-source-phone/cmd', { action, ...extra });
};
const core = (action, extra = {}) => {
  if (!PREVIEW) publish('jukebox/jb-core/cmd', { action, ...extra });
};
const phones = () => store.get('phones') || [];
const appPhones = () => store.get('helper')?.phones || [];
const activeId = () => store.get('source')?.active?.id;
const bluetoothOn = () => store.get('bluetooth')?.on !== false;

export function initPhones(blockTemplates, watchRoot, opts = {}) {
  templates = blockTemplates;
  openSettings = opts.openSettings || openSettings;
  store.watch(watchRoot, 'phones', () => follow?.());
  store.watch(watchRoot, 'pairing', () => follow?.());
  store.watch(watchRoot, 'bluetooth', () => follow?.());
  store.watch(watchRoot, 'helper', () => follow?.());
  store.watch(watchRoot, 'drivesIn', () => follow?.());
  store.watch(watchRoot, 'source', s => { follow?.(); offer(s); });
  // Switching reports back over the bus.
  store.watch(watchRoot, 'phoneSwitch', s => {
    if (!s || s.state === 'idle' || s.state === 'connecting' || !switching) return;
    switching = false;
    result('Phones', s.state === 'done' ? `Connected to ${s.name}.` : s.reason || `Couldn't connect to ${s.name || 'that phone'}.`);
  });
  // A phone turned away because another has the jukebox (the lock).
  store.watch(watchRoot, 'notice', n => {
    if (n?.type !== 'phone_turned_away' || popupOpen()) return;
    openPopup('notice', {
      title: `${n.name} tried to connect`,
      message: `${n.owner || 'Another phone'} has the jukebox. Switch phones from the Sources button (top right).`,
    });
    setTimeout(() => closePopup('notice'), RESULT_MS + 2000);
  });
}

function done() {
  follow = null;
  closePopup();
  if (returnTo) focusKey(returnTo);
}

function result(title, message) {
  follow = null;
  openPopup('notice', { title, message }, { onClose: () => returnTo && focusKey(returnTo) });
  setTimeout(() => closePopup('notice'), RESULT_MS);
}

// "Connected", "Last used 3 days ago".
function statusOf(p) {
  if (p.connected) return p.active ? 'Connected' : 'Connected, leaving';
  if (!p.last_connected) return 'Not connected yet';
  const days = Math.floor((Date.now() / 1000 - p.last_connected) / 86400);
  if (days < 1) return 'Last used today';
  if (days < 2) return 'Last used yesterday';
  if (days < 30) return `Last used ${days} days ago`;
  return 'Last used a while ago';
}

function phoneRows(prefix, onPick) {
  return phones().map(p => {
    const row = fromTemplate(templates['phone-row']).firstElementChild;
    bind(row, { name: p.name, status: statusOf(p) });
    row.toggleAttribute('data-connected', p.connected);
    makeStop(row, `${prefix}:${p.mac}`, () => onPick(p));
    return row;
  });
}

// Through jb-core, which phone has the jukebox (it connects the phone if
// it isn't): an app phone may have it, with this one connected beside it.
function connect(p) {
  if (p.connected && p.mac === activeId()) return done();
  core('use_source', { id: p.mac });
  if (p.connected) return result('Phones', `Switched to ${p.name}.`);
  switching = true;
  follow = null;
  openPopup('notice', { title: 'Phones', message: `Connecting to ${p.name}…` });
}

// -- the Sources badge: every phone and drive ------------------------------

function ago(t, word) {
  if (!t) return 'Not connected yet';
  const days = Math.floor((Date.now() / 1000 - t) / 86400);
  return days < 1 ? `${word} today` : days < 2 ? `${word} yesterday` : days < 30 ? `${word} ${days} days ago` : `${word} a while ago`;
}

// One row per phone (the one with the jukebox first) and per drive in.
function sources() {
  const active = activeId();
  const list = [];
  if (bluetoothOn()) {
    for (const p of phones()) {
      list.push({
        key: p.mac, kind: 'phone', name: p.name, phone: p,
        status: `Bluetooth · ${p.mac === active ? (p.connected ? 'In use' : 'In use, connecting') : p.connected ? 'Connected' : ago(p.last_connected, 'Last used')}`,
      });
    }
  }
  for (const p of appPhones()) {
    const id = `helper:${p.device_id}`;
    list.push({
      key: id, kind: 'helper', name: p.name || 'iPhone', phone: p,
      status: `App · ${id === active ? (p.reachable ? 'In use' : 'In use, not here') : p.reachable ? 'Here' : ago(p.last_seen, 'Last seen')}`,
    });
  }
  list.sort((a, b) => (b.key === active) - (a.key === active));
  for (const d of store.get('drivesIn') || []) {
    list.push({ key: `drive:${d.id}`, kind: 'drive', name: d.label || 'USB drive', drive: d,
      status: `Drive · ${d.song_count} song${d.song_count === 1 ? '' : 's'}, always on the board` });
  }
  return list;
}

function pickSource(src) {
  if (src.kind === 'drive') return result('Sources', `${src.name}'s songs are on the board while it's plugged in.`);
  if (src.key === activeId()) return done();
  core('use_source', { id: src.key });
  if (src.kind === 'phone' && !src.phone.connected) {
    switching = true;               // jb-source-phone connects it and says how it went
    follow = null;
    openPopup('notice', { title: 'Sources', message: `Connecting to ${src.name}…` });
    return;
  }
  result('Sources', `Switched to ${src.name}. Its songs are on the board.`);
}

export function openSwitcher() {
  returnTo = currentStopKey();
  const data = () => ({
    title: 'Sources', off: !bluetoothOn(), empty: !sources().length,
    addLabel: bluetoothOn() ? 'Add a phone' : 'Bluetooth settings',
  });
  const render = () => {
    const holder = document.querySelector('.jb-popup-frame[data-popup="phones"] [data-slot="phones"]');
    if (!holder) return;
    const active = activeId();
    holder.replaceChildren(...sources().map(src => {
      const row = fromTemplate(templates['phone-row']).firstElementChild;
      bind(row, { name: src.name, status: src.status });
      row.dataset.kind = src.kind;
      row.toggleAttribute('data-connected', src.key === active);
      row.toggleAttribute('data-active', src.key === active);
      makeStop(row, `popup:phones:${src.key}`, () => pickSource(src));
      return row;
    }));
    updatePopup('phones', data());
    refocus();
  };
  openPopup('phones', data(), {
    stops: {
      add: () => { follow = null; closePopup(); openSettings(bluetoothOn() ? 'bt:pair' : 'bt:power'); },
      close: done,
    },
  });
  render();
  const first = sources()[0];
  focusKey(first ? `popup:phones:${first.key}` : 'popup:phones:add');
  follow = render;
}

// A phone arrived while another has the jukebox: offer it, once. It goes
// if the phone leaves (or is chosen some other way) meanwhile.
let offered = null;
function offer(s) {
  const o = s?.offer;
  if (!o) {
    if (offered && popupOpen() === 'confirm') { closePopup('confirm'); if (returnTo) focusKey(returnTo); }
    offered = null;
    return;
  }
  if (o.id === offered || popupOpen()) return;
  offered = o.id;
  returnTo = currentStopKey();
  const holder = s.active?.connected ? s.active.name : null;
  openPopup('confirm', {
    title: `${o.name} is here`,
    message: holder ? `Switch to its music? ${holder}'s songs leave the board.` : 'Switch to its music?',
    yesLabel: 'Switch', noLabel: 'Not now',
  }, {
    stops: {
      yes: () => { offered = null; core('use_source', { id: o.id }); result('Sources', `Switched to ${o.name}.`); },
      no: () => { offered = null; core('decline_source'); done(); },
    },
  });
  focusKey('popup:confirm:yes');
}

// -- Settings > Bluetooth (the bluetooth block) --------------------------

export function fillBluetooth(el) {
  el.append(fromTemplate(templates.bluetooth));
  for (const stop of el.querySelectorAll('[data-stop]')) {
    const name = stop.dataset.stop;
    makeStop(stop, `bt:${name}`, () => (name === 'power' ? togglePower() : startPairing()));
  }
  const render = () => {
    const on = bluetoothOn();
    const connected = phones().find(p => p.connected && p.active);
    bind(el, {
      on,
      powerLabel: on ? 'On' : 'Off',
      note: on
        ? (connected ? `${connected.name} has the jukebox.`
          : store.get('source')?.active?.kind === 'helper'
            ? `${store.get('source').active.name} has the jukebox, through the app. Pick a phone below to switch to it.`
            : 'No phone connected. Pick one below, or connect from the phone.')
        : 'Off: the jukebox is invisible and no phone can connect.',
    });
    slot(el, 'phones')?.replaceChildren(...(on ? phoneRows('bt:phone', p => openPhone(p.mac)) : []));
  };
  store.watch(el, 'phones', render);
  store.watch(el, 'bluetooth', render);
  store.watch(el, 'source', render);
}

function togglePower() {
  returnTo = 'bt:power';
  if (!bluetoothOn()) return command('bluetooth', { on: true });
  const connected = phones().find(p => p.connected);
  if (!connected) return command('bluetooth', { on: false });
  openPopup('confirm', {
    title: 'Turn Bluetooth off?',
    message: `${connected.name} disconnects and the music stops. No phone can connect until it's back on.`,
    yesLabel: 'Turn off',
  }, {
    stops: {
      yes: () => { command('bluetooth', { on: false }); done(); },
      no: done,
    },
  });
  focusKey('popup:confirm:no');
}

function openPhone(mac) {
  returnTo = `bt:phone:${mac}`;
  const p = phones().find(x => x.mac === mac);
  if (!p) return done();
  openPopup('phone', { title: p.name, status: statusOf(p), connected: p.connected }, {
    stops: {
      connect: () => connect(p),
      disconnect: () => {
        command('disconnect', { mac });
        result('Phones', `Disconnected ${p.name}.`);
      },
      forget: () => openForget(p),
      back: done,
    },
  });
  focusKey(p.connected ? 'popup:phone:back' : 'popup:phone:connect');
}

function openForget(p) {
  openPopup('confirm', {
    title: `Forget ${p.name}?`,
    message: 'It\'s unpaired, and its songs, playlists and plays are removed from the jukebox. It can be paired again later.',
    yesLabel: 'Forget',
  }, {
    stops: {
      yes: () => {
        command('forget', { mac: p.mac });
        returnTo = 'bt:pair';
        result('Phones', `Forgot ${p.name}.`);
      },
      no: () => openPhone(p.mac),
    },
  });
  focusKey('popup:confirm:no');
}

// Pairing: the jukebox is visible for two minutes; a phone that pairs shows
// a code, and so does this screen; Pair here (and on the phone) accepts it.
function startPairing() {
  returnTo = 'bt:pair';
  let timer = null;
  let started = false;
  const view = s => {
    const left = s?.until ? Math.max(0, Math.round(s.until - Date.now() / 1000)) : 0;
    return {
      title: s?.state === 'confirm' ? 'Pair this phone?' : 'Pair a new phone',
      // Idle until jb-source-phone answers the request: show the steps already.
      waiting: !s || s.state === 'waiting' || s.state === 'idle',
      confirm: s?.state === 'confirm',
      done: s?.state === 'paired' || s?.state === 'failed',
      jukebox: s?.jukebox || 'the jukebox',
      countdown: s?.state === 'waiting' && s.until ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left` : '',
      code: s?.code ? `${s.code.slice(0, 3)} ${s.code.slice(3)}` : '',
      name: s?.name || 'the phone',
      result: s?.state === 'paired' ? `Paired with ${s.name}. It has the jukebox now.`
        : s?.state === 'failed' ? 'Pairing didn\'t finish. Try again.' : '',
    };
  };
  const stop = () => {
    clearInterval(timer);
    follow = null;
  };
  openPopup('pairing', view({ state: 'waiting' }), {
    stops: {
      accept: () => command('pair_confirm', { ok: true }),
      cancel: () => {
        if (store.get('pairing')?.state === 'confirm') command('pair_confirm', { ok: false });
        else command('pair_stop');
        stop();
        done();
      },
    },
    onClose: stop,
  });
  focusKey('popup:pairing:cancel');
  command('pair_start');
  follow = () => {
    const s = store.get('pairing');
    if (s?.state === 'idle') {
      // The window closed with no phone (timed out).
      if (started) { stop(); done(); }
      return;
    }
    started = true;
    updatePopup('pairing', view(s));
    if (s?.state === 'confirm') focusKey('popup:pairing:accept');
    if (s?.state === 'paired' || s?.state === 'failed') {
      stop();
      setTimeout(() => { if (popupOpen() === 'pairing') done(); }, RESULT_MS);
    }
  };
  timer = setInterval(() => popupOpen() === 'pairing' && updatePopup('pairing', view(store.get('pairing'))), 1000);
}
