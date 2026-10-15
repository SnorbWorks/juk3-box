// Phones and Bluetooth on the cabinet (dev plan Phase 4 and the Phase 7
// screens). jb-source-phone does the work; these are its screens.
//
//   The phone badge (top right): a quick switcher between phones already
//   paired, plus "Add a phone", which goes to Settings > Bluetooth.
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
const phones = () => store.get('phones') || [];
const bluetoothOn = () => store.get('bluetooth')?.on !== false;

export function initPhones(blockTemplates, watchRoot, opts = {}) {
  templates = blockTemplates;
  openSettings = opts.openSettings || openSettings;
  store.watch(watchRoot, 'phones', () => follow?.());
  store.watch(watchRoot, 'pairing', () => follow?.());
  store.watch(watchRoot, 'bluetooth', () => follow?.());
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
      message: `${n.owner || 'Another phone'} has the jukebox. Switch phones from the phone badge.`,
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

function connect(p) {
  if (p.connected && p.active) return done();
  switching = true;
  command('switch', { mac: p.mac });
  follow = null;
  openPopup('notice', { title: 'Phones', message: `Connecting to ${p.name}…` });
}

// -- the badge: switch between paired phones -----------------------------

export function openSwitcher() {
  returnTo = currentStopKey();
  const data = () => ({
    title: 'Phones', off: !bluetoothOn(), empty: bluetoothOn() && !phones().length,
    addLabel: bluetoothOn() ? 'Add a phone' : 'Bluetooth settings',
  });
  const render = () => {
    const holder = document.querySelector('.jb-popup-frame[data-popup="phones"] [data-slot="phones"]');
    if (!holder) return;
    holder.replaceChildren(...(bluetoothOn() ? phoneRows('popup:phones', connect) : []));
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
  const first = bluetoothOn() && phones()[0];
  focusKey(first ? `popup:phones:${first.mac}` : 'popup:phones:add');
  follow = render;
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
        ? (connected ? `${connected.name} has the jukebox.` : 'No phone connected. Pick one below, or connect from the phone.')
        : 'Off: the jukebox is invisible and no phone can connect.',
    });
    slot(el, 'phones')?.replaceChildren(...(on ? phoneRows('bt:phone', p => openPhone(p.mac)) : []));
  };
  store.watch(el, 'phones', render);
  store.watch(el, 'bluetooth', render);
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
