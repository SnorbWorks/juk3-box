// Settings > Sources: where the music comes from, as in the admin's Sources
// tab. The Settings screen lists the three kinds (the sources block); each
// opens its own screen: sources-bluetooth (the bluetooth block, phones.js),
// sources-app (the juk3box Helper app) and sources-drives (USB drives).
// One phone has the jukebox at a time, Bluetooth or the app (jb-core's
// phone_choice.py); drives' songs are always on the board.

import { bind, fromTemplate, slot } from './bind.js';
import { publish } from './bus.js';
import { focusKey, makeStop, refocus } from './focus.js';
import { closePopup, openPopup } from './popup.js';
import { store } from './store.js';

const RESULT_MS = 4000;
let templates = {};

export const SOURCE_SCREENS = {
  'sources-bluetooth': { title: 'Bluetooth', block: 'bluetooth' },
  'sources-app': { title: 'App', block: 'helper-app' },
  'sources-drives': { title: 'Drives', block: 'drives' },
};

const helper = (action, extra = {}) => publish('jukebox/jb-source-helper/cmd', { action, ...extra });
const core = (action, extra = {}) => publish('jukebox/jb-core/cmd', { action, ...extra });
const appPhones = () => store.get('helper')?.phones || [];
const activeId = () => store.get('source')?.active?.id;
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

export function initSources(blockTemplates) {
  templates = blockTemplates;
}

function notice(title, message, back) {
  openPopup('notice', { title, message }, { onClose: () => back && focusKey(back) });
  setTimeout(() => closePopup('notice'), RESULT_MS);
}

function confirm(title, message, yesLabel, back, onYes) {
  const done = () => { closePopup(); focusKey(back); };
  openPopup('confirm', { title, message, yesLabel }, { stops: { yes: () => { closePopup(); onYes(); }, no: done } });
  focusKey('popup:confirm:no');
}

// A short list of things to do, as rows (the choices popup).
function actionsFor(title, options, back) {
  const done = () => { closePopup(); focusKey(back); };
  openPopup('choices', { title }, { stops: { close: done } });
  const holder = document.querySelector('.jb-popup-frame[data-popup="choices"] [data-slot="choices"]');
  holder?.replaceChildren(...options.map((o, i) => {
    const row = fromTemplate(templates['phone-row']).firstElementChild;
    bind(row, { name: o.label, status: o.hint || '' });
    makeStop(row, `popup:choices:${i}`, () => { closePopup(); o.run(); });
    return row;
  }));
  refocus();
  focusKey('popup:choices:0');
}

// -- Settings: the three kinds (the sources block) -------------------------

export function fillSources(el, ctx) {
  el.append(fromTemplate(templates.sources));
  for (const stop of el.querySelectorAll('[data-stop]')) {
    const kind = stop.dataset.stop;
    makeStop(stop, `sources:${kind}`, () => ctx.actions.nav(`sources-${kind}`));
  }
  const render = () => {
    const bt = store.get('bluetooth')?.on !== false;
    const btPhone = (store.get('phones') || []).find(p => p.connected);
    const apps = appPhones();
    const active = store.get('source')?.active;
    const drives = store.get('drivesIn') || [];
    bind(el, {
      bluetooth: !bt ? 'Off' : btPhone ? btPhone.name : 'On',
      app: active?.kind === 'helper' ? active.name : apps.length ? plural(apps.length, 'phone') : 'No phones',
      drives: drives.length ? plural(drives.length, 'drive') : 'None',
      note: active ? `${active.name} has the jukebox${active.kind === 'helper' ? '' : ', over Bluetooth'}.`
        : 'No phone has the jukebox yet. The first one to connect gets it.',
    });
  };
  for (const key of ['bluetooth', 'phones', 'helper', 'source', 'drivesIn']) store.watch(el, key, render);
}

// -- Sources > App (the helper-app block) ------------------------------------

function appStatus(p) {
  const has = p.device_id && `helper:${p.device_id}` === activeId();
  return [
    has ? 'Has the jukebox' : p.reachable ? 'Here' : 'Not here',
    p.songs ? plural(p.songs, 'song') : null,
  ].filter(Boolean).join(' · ');
}

export function fillHelperApp(el) {
  el.append(fromTemplate(templates['helper-app']));
  const settings = () => store.get('helperSettings') || {};
  const toggles = {
    pairing: () => helper('settings', { allow_pairing: !settings().allow_pairing }),
    ble: () => helper('settings', { ble: settings().ble === false }),
  };
  for (const stop of el.querySelectorAll('[data-stop]')) {
    const name = stop.dataset.stop;
    makeStop(stop, `app:${name}`, () => toggles[name]?.());
  }
  const render = () => {
    const s = settings();
    const phones = appPhones();
    bind(el, {
      intro: 'iPhones that play their Apple Music library through the juk3box Helper app, sent here over AirPlay.',
      none: phones.length ? '' : 'No phones yet. Install juk3box Helper and open it on the same Wi-Fi; turn on "Allow new phones to pair" first.',
      pairing: s.allow_pairing ? 'On' : 'Off',
      ble: s.ble === false ? 'Off' : 'On',
      note: s.allow_pairing ? 'An app that finds the jukebox shows a code here to type in. It turns off by itself once a phone pairs.'
        : 'Only phones already paired can connect.',
    });
    slot(el, 'phones')?.replaceChildren(...phones.map(p => {
      const row = fromTemplate(templates['phone-row']).firstElementChild;
      const id = `helper:${p.device_id}`;
      bind(row, { name: p.name || 'iPhone', status: appStatus(p) });
      row.toggleAttribute('data-connected', id === activeId());
      makeStop(row, `app:phone:${p.device_id}`, () => openAppPhone(p));
      return row;
    }));
  };
  for (const key of ['helper', 'helperSettings', 'source']) store.watch(el, key, render);
}

function openAppPhone(p) {
  const back = `app:phone:${p.device_id}`;
  const id = `helper:${p.device_id}`;
  const name = p.name || 'iPhone';
  const has = id === activeId();
  actionsFor(name, [
    has ? null : { label: 'Use this phone', hint: 'Its songs on the board', run: () => {
      core('use_source', { id });
      notice('Sources', `Switched to ${name}.`, back);
    } },
    has && p.reachable ? { label: 'Read its library again', run: () => {
      helper('sync');
      notice('Sources', `Reading ${name}'s library again…`, back);
    } } : null,
    { label: 'Forget', hint: 'Unpair it', run: () => confirm(`Forget ${name}?`,
      'The app is unpaired, and its songs, playlists and plays are removed from the jukebox.', 'Forget', back, () => {
        helper('forget', { device_id: p.device_id });
        notice('Sources', `Forgot ${name}.`, 'app:pairing');
      }) },
  ].filter(Boolean), back);
}

// -- Sources > Drives (the drives block) -------------------------------------

export function fillDrives(el) {
  el.append(fromTemplate(templates.drives));
  const render = () => {
    const drives = store.get('drivesIn') || [];
    bind(el, {
      note: drives.length ? 'Their songs are always on the board, beside the phone\'s. Eject one before pulling it out.'
        : 'None plugged in. Plug in a USB drive with music on it and its songs join the board.',
    });
    slot(el, 'drives')?.replaceChildren(...drives.map(d => {
      const row = fromTemplate(templates['phone-row']).firstElementChild;
      const label = d.label || 'USB drive';
      bind(row, { name: label, status: plural(d.song_count || 0, 'song') });
      makeStop(row, `drives:${d.id}`, () => confirm(`Eject ${label}?`,
        'Its songs leave the board until it\'s plugged in again.', 'Eject', `drives:${d.id}`, () => {
          publish('jukebox/jb-source-local/cmd', { action: 'eject', id: d.id });
          notice('Drives', `Ejecting ${label}. You can pull it out in a moment.`, null);
        }));
      return row;
    }));
  };
  store.watch(el, 'drivesIn', render);
}
