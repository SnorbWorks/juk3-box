// Managing phones from the cabinet (dev plan Phase 4 and the Phase 7
// screens): the paired phones, connecting one (the jukebox switches to
// it), disconnecting, forgetting, and pairing a new one with the code
// confirmed here. jb-source-phone does the work; these are its screens.
//
// The popups (popup-phones, popup-phone, popup-forget, popup-pairing and
// each phone's phone-row) are engine blocks a theme can restyle or replace.

import { bind, fromTemplate, slot } from './bind.js';
import { PREVIEW, publish } from './bus.js';
import { focusKey, makeStop, refocus } from './focus.js';
import { closePopup, openPopup, popupOpen, updatePopup } from './popup.js';
import { store } from './store.js';

const RESULT_MS = 4000;
let templates = {};
let returnTo = null;         // the stop to go back to when the menus close
let unwatch = null;          // the open screen's store watch

const command = (action, extra = {}) => {
  if (!PREVIEW) publish('jukebox/jb-source-phone/cmd', { action, ...extra });
};

export function initPhones(blockTemplates, watchRoot) {
  templates = blockTemplates;
  // The open screen follows the phone list and the pairing state.
  store.watch(watchRoot, 'phones', () => unwatch?.());
  store.watch(watchRoot, 'pairing', () => unwatch?.());
  // Switching and pairing report back over the bus; whatever screen is open
  // shows it (the store watch lives as long as the page).
  store.watch(watchRoot, 'phoneSwitch', s => {
    if (!s || s.state === 'idle' || !switching) return;
    if (s.state === 'done') result('Phones', `Connected to ${s.name}.`);
    else if (s.state === 'failed') result('Phones', s.reason || `Couldn't connect to ${s.name || 'that phone'}.`);
    if (s.state !== 'connecting') switching = false;
  });
  store.watch(watchRoot, 'notice', n => {
    if (n?.type !== 'phone_turned_away' || popupOpen()) return;
    openPopup('notice', {
      title: `${n.name} tried to connect`,
      message: `${n.owner || 'Another phone'} has the jukebox. Switch phones from the Phones menu.`,
    });
    setTimeout(() => closePopup('notice'), RESULT_MS + 2000);
  });
}

let switching = false;

function close() {
  unwatch = null;
  closePopup();
  if (returnTo) focusKey(returnTo);
}

function result(title, message) {
  unwatch = null;
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

export function openPhones(from) {
  if (from !== undefined) returnTo = from;
  const list = () => store.get('phones') || [];
  const render = () => {
    const holder = document.querySelector('.jb-popup-frame[data-popup="phones"] [data-slot="phones"]');
    if (!holder) return;
    const rows = list().map(p => {
      const row = fromTemplate(templates['phone-row']).firstElementChild;
      bind(row, { name: p.name, status: statusOf(p) });
      row.toggleAttribute('data-connected', p.connected);
      makeStop(row, `popup:phones:${p.mac}`, () => openPhone(p.mac));
      return row;
    });
    holder.replaceChildren(...rows);
    updatePopup('phones', { title: 'Phones', empty: !rows.length });
    refocus();   // the highlighted row was just replaced
  };
  openPopup('phones', { title: 'Phones', empty: !list().length }, {
    stops: { pair: () => startPairing(), close },
  });
  render();
  const first = list()[0];
  focusKey(first ? `popup:phones:${first.mac}` : 'popup:phones:pair');
  unwatch = render;
}

function openPhone(mac) {
  const p = (store.get('phones') || []).find(x => x.mac === mac);
  if (!p) return openPhones();
  openPopup('phone', { title: p.name, status: statusOf(p), connected: p.connected }, {
    stops: {
      connect: () => {
        switching = true;
        command('switch', { mac });
        openPopup('notice', { title: 'Phones', message: `Connecting to ${p.name}…` });
      },
      disconnect: () => {
        command('disconnect', { mac });
        result('Phones', `Disconnected ${p.name}.`);
      },
      forget: () => openForget(p),
      back: () => openPhones(),
    },
  });
  focusKey(p.connected ? 'popup:phone:back' : 'popup:phone:connect');
}

function openForget(p) {
  openPopup('forget', { title: `Forget ${p.name}?` }, {
    stops: {
      forget: () => {
        command('forget', { mac: p.mac });
        result('Phones', `Forgot ${p.name}.`);
      },
      cancel: () => openPhone(p.mac),
    },
  });
  focusKey('popup:forget:cancel');
}

// Pairing: the jukebox is visible for two minutes; a phone that pairs shows
// a code, and so does this screen; Pair here (and on the phone) accepts it.
function startPairing() {
  let timer = null;
  let asked = false;
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
        : s?.state === 'failed' ? 'Pairing didn\'t finish. Try again from the Phones menu.' : '',
    };
  };
  const stop = () => {
    clearInterval(timer);
    unwatch = null;
  };
  openPopup('pairing', view({ state: 'waiting' }), {
    stops: {
      accept: () => command('pair_confirm', { ok: true }),
      cancel: () => {
        const s = store.get('pairing');
        if (s?.state === 'confirm') command('pair_confirm', { ok: false });
        else command('pair_stop');
        stop();
        openPhones();
      },
    },
    onClose: stop,
  });
  focusKey('popup:pairing:cancel');
  command('pair_start');
  const render = () => {
    const s = store.get('pairing');
    if (s?.state === 'idle') {
      // The window closed with no phone (timed out): back to the list.
      if (asked) { stop(); openPhones(); }
      return;
    }
    asked = true;
    updatePopup('pairing', view(s));
    if (s?.state === 'confirm') focusKey('popup:pairing:accept');
    if (s?.state === 'paired' || s?.state === 'failed') {
      stop();
      setTimeout(() => { if (popupOpen() === 'pairing') close(); }, RESULT_MS);
    }
  };
  unwatch = render;
  timer = setInterval(() => popupOpen() === 'pairing' && updatePopup('pairing', view(store.get('pairing'))), 1000);
}
