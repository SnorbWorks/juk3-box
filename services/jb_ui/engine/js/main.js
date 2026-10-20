// JUKE.BOX UI engine: loads the active theme and builds the screen from
// its layouts. Behavior lives here; the look lives in the theme.

import { albumSongs, openFilter, openList, pick, setSort, upLevel } from './board.js';
import { playList, startAutoplay } from './lists.js';
import { connectBus, PREVIEW, publish } from './bus.js';
import { startFeatured } from './featured.js';
import { click, currentMode, currentStopKey, enterMode, focusKey, goToRest, refocus, resetScroll, rotate } from './focus.js';
import { renderLayout } from './layout.js';
import { nowPlayingData, playingLibraryTrack } from './np.js';
import { closePopup, initPopups, openPopup, popupOpen, updatePopup } from './popup.js';
import { initPhones, openSwitcher } from './phones.js';
import { initWholeHome } from './wholehome.js';
import { initDisplay } from './display.js';
import { sectionView } from './sections.js';
import { store } from './store.js';
import { fetchTheme, applyTheme } from './theme.js';
import { sized } from './tracks.js';

const VOLUME_STEP = 0.05;   // per knob detent
const NOTICE_MS = 4000;
const IDLE_CHECK_MS = 10000;
const OVERLAY_TICK_MS = 1000;

// Used when a theme has no layout for the See All view.
const DEFAULT_COLLECTION_LAYOUT = {
  class: 'jb-collection-screen',
  children: [{ block: 'header' }, { block: 'collection', focus: 'collection' }],
};
// The cabinet's Settings, unless the theme lays it out itself.
const DEFAULT_SETTINGS_LAYOUT = {
  class: 'jb-settings-screen',
  children: [
    { class: 'top-bar', focus: 'top', children: [{ block: 'header' }] },
    { class: 'jb-settings-sections', focus: 'settings', children: [{ block: 'bluetooth' }, { block: 'wholehome' }, { block: 'display' }] },
  ],
};

let theme = null;
const startedAt = Date.now();
const html = document.documentElement;
const history = [];  // screens to go Back to

const actions = {
  nav(name) {
    if (name === 'volume') volumeMode();
    else if (name === 'shuffle') toggleShuffle();
    else if (name === 'now-playing') openNowPlaying();
    else {
      // All Songs means all of them: a plain title list, from page 1.
      if (name === 'all-songs') setSort('title');
      showScreen(name);
    }
  },
  // A song card or a keypad pick: jb-core plays it now if nothing is
  // playing, otherwise queues it.
  pick(track) {
    publish('jukebox/jb-core/cmd', { action: 'select', track });
  },
  openSort(sort) {
    setSort(sort);
    showScreen('all-songs');
  },
  openFilter(field, value) {
    openFilter(field, value);
    showScreen('all-songs');
  },
  // A module's playlist (lists.js): open it on the board, or play it all.
  openList(name) {
    openList(name);
    showScreen('all-songs');
  },
  playList(name) {
    playList(name);
  },
  openCollection(type) {
    const view = sectionView(type, context());
    if (!view) return;
    store.set('collection', view);
    showScreen('collection');
  },
  // Back: out of an artist (album, genre) to the list of them, else to the
  // previous screen, else Home.
  back() {
    if (store.get('screen') === 'all-songs' && upLevel()) return;
    showScreen(history.pop() || 'home', { remember: false });
  },
  phones,
};

function context() {
  return {
    base: theme.base,
    blocks: theme.blocks,
    icons: theme.icons,
    browseImages: theme.browse_images,
    cardIcons: theme.card_icons || {},
    moduleBlocks: theme.module_blocks || {},
    settings: theme.settings,
    actions,
  };
}

// Screens left are kept, hidden, and shown again as they were: building
// Home or All Songs from scratch took one to five seconds on the Pi 4.
// Not a collection (its layout changes with what it shows), and not in
// the admin preview (the layout there is being edited).
const parked = new Map();   // screen name -> its element
const keeps = name => !PREVIEW && name !== 'collection';

function showScreen(name, { remember = true } = {}) {
  const holder = document.querySelector('jb-screen');
  if (!holder) return;
  const from = store.get('screen');
  if (name === 'home') history.length = 0;
  else if (remember && from && from !== name) history.push(from);
  const showing = holder.querySelector(':scope > :not([data-parked])');
  if (showing && from && from !== name && keeps(from) && !showing.dataset.placeholder) {
    resetScroll(showing);
    showing.dataset.parked = '';
    showing.inert = true;
    parked.set(from, showing);
  } else {
    showing?.remove();
  }
  const layout = theme.layouts[name]
    || { collection: DEFAULT_COLLECTION_LAYOUT, settings: DEFAULT_SETTINGS_LAYOUT }[name] || null;
  const kept = parked.get(name);
  if (kept) {
    parked.delete(name);
    kept.inert = false;
    delete kept.dataset.parked;
  } else if (layout) {
    holder.append(renderLayout(layout, context(), PREVIEW ? name : undefined));
  } else {
    // The theme has no layout for this screen (only home and all-songs
    // are required). The engine's own version comes later; for now, say so.
    const missing = document.createElement('div');
    missing.dataset.placeholder = name;
    holder.append(missing);
  }
  html.dataset.screen = name;
  store.set('screen', name);
  refocus();
}

// Turning the knob changes the volume until a click or a few idle seconds.
function volumeMode() {
  // The knob's own running total is what's shown: a confirmation for an
  // older step can arrive after newer steps and would snap it backwards.
  let level = store.get('volume')?.volume ?? 0;
  // --jb-volume goes on the popup, not the page: a variable changed on
  // <html> restyles every element (~225 ms a knob step on the Pi 4).
  const show = () => updatePopup('volume', { title: 'Volume', percent: Math.round(level * 100) },
    { '--jb-volume': level.toFixed(3) });
  openPopup('volume', {});
  show();
  enterMode('volume', {
    rotate(steps) {
      const delta = steps * VOLUME_STEP;
      level = Math.min(1, Math.max(0, level + delta));
      publish('jukebox/jb-audio/cmd', { action: 'volume_delta', value: delta });
      show();
    },
    exit() {
      closePopup('volume');
    },
  });
}

function toggleShuffle() {
  const on = !!store.get('queue')?.shuffle_active;
  // jb-core answers with a new queue state, which updates the button.
  publish('jukebox/jb-core/cmd', { action: on ? 'shuffle_off' : 'shuffle_on' });
}

// The Now Playing overlay: the song large, over everything, with Favorite
// and Close. The knob stays inside it until it closes.
function openNowPlaying() {
  const returnTo = currentStopKey();
  const render = () => {
    const d = nowPlayingData();
    const fav = !!playingLibraryTrack()?.favorite;
    updatePopup('now-playing', {
      ...d,
      title: 'Now Playing',
      song: d.title || '',
      cover: sized(d.cover, 800),
      favorite: fav,
      favoriteLabel: fav ? 'Favorite' : 'Add to Favorites',
      favoriteIcon: theme.icons.favorite,
      closeIcon: theme.icons.close,
    }, { '--jb-progress': d.progress.toFixed(4) });
    document.querySelector('.jb-npo-favorite')?.toggleAttribute('data-on', fav);
  };
  const timer = setInterval(() => popupOpen() === 'now-playing' && render(), OVERLAY_TICK_MS);
  openPopup('now-playing', { title: 'Now Playing' }, {
    stops: {
      favorite: () => {
        const t = playingLibraryTrack();
        if (!t) return;
        publish('jukebox/jb-library/cmd', { action: 'favorite', title: t.title, artist: t.artist, value: !t.favorite });
      },
      close: () => closePopup('now-playing'),
    },
    onClose: () => {
      clearInterval(timer);
      focusKey(returnTo);
    },
  });
  render();
  refocus();
}

// The power menu (holding Reset; jb-core's power.py decides when). The
// knob turns between Reboot, Shut down and Cancel and starts on Cancel, so
// a stray click never takes the jukebox down; jb-core closes it on a choice,
// a Reset click or its timeout, and this follows jukebox/jb-core/power.
function openPower() {
  const returnTo = currentStopKey();
  const choose = choice => publish('jukebox/jb-core/cmd', { action: 'power', choice });
  const render = () => {
    const p = store.get('power');
    const left = p?.until ? Math.max(0, Math.ceil(p.until - Date.now() / 1000)) : null;
    updatePopup('power', {
      title: 'Power',
      message: 'Reboot or shut down the jukebox?',
      countdown: left === null ? '' : `Cancels in ${left} s`,
    });
  };
  const timer = setInterval(() => popupOpen() === 'power' && render(), 1000);
  openPopup('power', { title: 'Power' }, {
    stops: { reboot: () => choose('reboot'), shutdown: () => choose('shutdown'), cancel: () => choose('cancel') },
    onClose: () => {
      clearInterval(timer);
      focusKey(returnTo);
    },
  });
  render();
  focusKey('popup:power:cancel');
}

// Picking an album plays it (the user's design): once its songs are known --
// at once if they were fetched before, else when jb-crawl's fetch lands --
// they go to jb-core as one pick, in album order. A failed fetch drops it.
function watchAlbumPlay() {
  const play = () => {
    const want = store.get('albumPlay');
    if (!want) return;
    const fetch = store.get('albumFetch');
    if (fetch?.name === want.name && fetch.state === 'failed') {
      store.set('albumPlay', null);
      return;
    }
    const songs = albumSongs(store.get('tracks') || [], want.name);
    if (!songs) return;                     // not fetched yet
    store.set('albumPlay', null);
    if (songs.length) publish('jukebox/jb-core/cmd', { action: 'select_album', album: want.name, tracks: songs });
  };
  store.watch(html, 'albumPlay', play);
  store.watch(html, 'albums', play);
  store.watch(html, 'albumFetch', play);
}

function watchPower() {
  store.watch(html, 'power', p => {
    if (p?.state === 'menu') {
      if (popupOpen() !== 'power') openPower();
    } else {
      closePopup('power');
      if (p?.state === 'rebooting' || p?.state === 'shutting_down') {
        openPopup('notice', {
          title: p.state === 'rebooting' ? 'Rebooting' : 'Shutting down',
          message: p.state === 'rebooting' ? 'Back in a minute.' : 'Goodbye!',
        });
      }
    }
  });
}

// The phone badge: switch between paired phones (phones.js).
function phones() {
  openSwitcher();
}

function watchSystem() {
  // Only on a change: anything written to <html> can restyle the page.
  store.watch(html, 'phone', p => {
    const phone = p?.connected ? 'connected' : 'disconnected';
    if (html.dataset.phone !== phone) html.dataset.phone = phone;
  });
  // Lets themes animate things (the EQ icon) while a song plays.
  store.watch(html, 'nowPlaying', np => {
    const playing = np?.status === 'playing';
    if (html.hasAttribute('data-playing') !== playing) html.toggleAttribute('data-playing', playing);
  });

  // A notice from jb-core shows briefly, unless the knob is busy: a pick
  // with no phone connected.
  store.watch(html, 'notice', n => {
    if (n?.type !== 'connect_device' || currentMode() !== 'select' || popupOpen()) return;
    openPopup('notice', { title: 'Please connect a device', message: 'Connect your phone over Bluetooth to play music.' });
    setTimeout(() => closePopup('notice'), NOTICE_MS);
  });

  // The phone connecting and its song list being read (jb-source-phone/sync):
  // an overlay until it's done. Picks made meanwhile wait in the queue and
  // play when it's ready (jb-core). If the read fails, say what to do.
  const overlay = document.createElement('div');
  overlay.id = 'jb-sync-overlay';
  overlay.hidden = true;
  overlay.innerHTML = '<div class="jb-sync-box"><div class="jb-sync-spinner"></div>'
    + '<div class="jb-sync-title">Connecting and updating library…</div>'
    + '<div class="jb-sync-message">Please wait. A song you pick now plays as soon as it\'s ready.</div></div>';
  document.body.append(overlay);
  let syncWas = null;
  store.watch(html, 'phoneSync', s => {
    const state = s?.state;
    const busy = state === 'connecting' || state === 'syncing';
    if (overlay.hidden === busy) overlay.hidden = !busy;
    if (state === 'failed' && syncWas && syncWas !== 'failed' && currentMode() === 'select' && !popupOpen()) {
      openPopup('notice', {
        title: 'Couldn\'t read your phone\'s songs',
        message: 'Turn Bluetooth off and on again on your phone.',
      });
      setTimeout(() => closePopup('notice'), NOTICE_MS * 2);
    }
    syncWas = state;
  });

  // A USB drive was plugged in and read (jb-source-local): its songs are
  // now in the library, alongside the phone's.
  store.watch(html, 'driveEvent', e => {
    if (e?.type !== 'drive_added' || currentMode() !== 'select' || popupOpen()) return;
    const songs = e.songs === 1 ? '1 song' : `${e.songs} songs`;
    openPopup('notice', { title: 'Music drive connected', message: `${e.label || 'USB drive'}: ${songs} ready to play.` });
    setTimeout(() => closePopup('notice'), NOTICE_MS);
  });

  // A drive with new pictures or videos (jb-admin/drive-offers): ask before
  // importing them into the media library. Asked once per drive here; the
  // answer goes to jb-admin, which offers it again only after a replug.
  // Waits for the screen to be free (a notice, a menu), checking again
  // every few seconds.
  const asked = new Set();
  let showing = null;                         // the drive being asked about
  const offerImport = () => {
    const offers = store.get('driveOffers') || [];
    for (const id of asked) if (!offers.some(o => o.id === id)) asked.delete(id);
    // Unplugged (or imported from the admin) while asking: stop asking.
    if (showing && !offers.some(o => o.id === showing.id)) showing.close();
    const o = offers.find(o => !asked.has(o.id));
    if (!o || currentMode() !== 'select' || popupOpen()) return;
    asked.add(o.id);
    const count = (n, one, many) => (n === 1 ? `1 ${one}` : `${n} ${many}`);
    const what = [o.pictures ? count(o.pictures, 'picture', 'pictures') : null,
      o.videos ? count(o.videos, 'video', 'videos') : null].filter(Boolean).join(' and ');
    let answered = false;
    const back = currentStopKey();
    const answer = yes => {
      if (answered) return;
      answered = true;
      if (yes !== null) publish('jukebox/jb-admin/cmd', { action: yes ? 'import_drive' : 'decline_drive', id: o.id });
      closePopup('confirm');
      focusKey(back);
    };
    showing = { id: o.id, close: () => answer(null) };
    openPopup('confirm', {
      title: 'Would you like to import?',
      message: `${o.label || 'This USB drive'} has ${what}. Copy them into the media library?`,
      yesLabel: 'Import',
    }, {
      stops: { yes: () => answer(true), no: () => answer(false) },
      // Closed some other way (another popup): ask again later.
      onClose: () => {
        showing = null;
        if (!answered) asked.delete(o.id);
      },
    });
    focusKey('popup:confirm:no');
  };
  store.watch(html, 'driveOffers', offerImport);
  setInterval(offerImport, 5000);

  // A pick that didn't play: jb-core's pick doctor says why and what to
  // do ("Turn Bluetooth off and on again on your phone"), or that it's
  // already trying again. Shown over another notice, not over a menu.
  let problemTimer;
  store.watch(html, 'pickProblem', p => {
    const open = popupOpen();
    if (!p || currentMode() !== 'select' || (open && open !== 'notice' && open !== 'now-playing')) return;
    const song = p.title ? `"${p.title}": ` : '';
    openPopup('notice', { title: p.headline, message: `${song}${p.advice}` });
    clearTimeout(problemTimer);
    problemTimer = setTimeout(() => closePopup('notice'), p.problem === 'retrying' ? NOTICE_MS : NOTICE_MS * 3);
  });

  // The keypad: a letter brings up the board (so you can see what you're
  // picking), and letter + digit picks that slot. Picking an artist
  // (album, genre) opens its songs; picking a song sends it to jb-core,
  // which plays it now or queues it.
  store.watch(html, 'entry', e => {
    if (e?.state !== 'partial') return;
    if (popupOpen() === 'now-playing') closePopup('now-playing');
    if (store.get('screen') !== 'all-songs') showScreen('all-songs');
  });
  store.watch(html, 'selection', s => {
    if (!Number.isInteger(s?.index)) return;
    const track = pick(s.index);
    if (track) actions.pick(track);
  });
}

// Idle with nothing playing for the owner's set time: back to Home. The
// highlight returns to rest much sooner (focus.js); the screen only moves
// once the jukebox is truly unattended.
function watchIdle() {
  setInterval(() => {
    const minutes = theme.settings.idle_home_minutes;
    // A preview stays on the screen the page builder is showing.
    if (PREVIEW || !minutes || (store.get('screen') === 'home' && !popupOpen())) return;
    if (store.get('nowPlaying')?.status === 'playing') return;
    const last = Math.max(store.get('activity') || 0, startedAt);
    if (Date.now() - last < minutes * 60000) return;
    closePopup();
    showScreen('home');
  }, IDLE_CHECK_MS);
}

// A real keyboard works too, for development: arrows turn, Enter clicks.
function watchKeyboard() {
  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowRight') rotate(1);
    else if (e.key === 'ArrowLeft') rotate(-1);
    else if (e.key === 'Enter') click();
  });
}

async function start() {
  // Tell the kiosk launcher the splash is on screen, picture and all: it
  // can now show this window over sway's identical one
  // (deploy/kiosk/kiosk-chromium.sh).
  const splashPicture = document.querySelector('#jb-splash img');
  if (!PREVIEW && splashPicture) {
    splashPicture.decode().catch(() => {}).then(() =>
      requestAnimationFrame(() => setTimeout(() => fetch('/api/kiosk-painted', { method: 'POST' }).catch(() => {}), 0)));
  }
  // Then the live splash (index.html) fades in over the still one, which it
  // matches, and its bars start once it's fully in.
  const splash = document.getElementById('jb-splash');
  const liveLogo = document.querySelector('#jb-splash .logo img');
  if (!PREVIEW && splash && liveLogo) {
    liveLogo.decode().then(() => {
      splash.classList.add('live-on');
      setTimeout(() => splash.classList.add('playing'), 900);
    }).catch(() => {});
  }
  theme = await fetchTheme();
  await applyTheme(theme);
  store.set('settings', theme.settings);
  initPopups(theme.blocks);
  initWholeHome(theme.blocks);
  initDisplay(theme.blocks);
  initPhones(theme.blocks, html, {
    // "Add a phone" on the badge: Settings, at its Bluetooth section.
    openSettings: key => {
      showScreen('settings');
      focusKey(key);
    },
  });
  watchSystem();
  watchPower();
  watchAlbumPlay();
  watchIdle();
  startFeatured();
  startAutoplay(context());
  document.getElementById('jb-root').replaceChildren(renderLayout(theme.layouts.frame, context(), PREVIEW ? 'frame' : undefined));
  // The admin preview can open on any screen (?screen=all-songs).
  const first = PREVIEW && new URLSearchParams(location.search).get('screen');
  showScreen(first && theme.layouts[first] ? first : 'home');
  goToRest();
  watchKeyboard();
  connectBus();
  reveal();
}

// Startup: the splash (index.html) stays up while the screen fills in
// behind it -- the library and what's playing arrive over the bus, then the
// pictures on show load -- and fades out once, instead of the screen
// visibly building itself piece by piece. Never more than a few seconds:
// with no phone or no bus it shows what there is.
const READY_WAIT_MS = 6000;
const PICTURES_WAIT_MS = 2500;

async function reveal() {
  const splash = document.getElementById('jb-splash');
  if (!splash) return;
  if (PREVIEW) return splash.remove();
  const until = (ms, ...waits) => Promise.race([Promise.all(waits), new Promise(r => setTimeout(r, ms))]);
  const arrived = key => new Promise(resolve => {
    if (store.get(key) !== undefined) return resolve();
    store.watch(splash, key, v => { if (v !== undefined) resolve(); });
  });
  await until(READY_WAIT_MS, arrived('tracks'), arrived('nowPlaying'), arrived('phone'));
  // Let the blocks draw with that data, then wait for what's on screen.
  await new Promise(r => requestAnimationFrame(() => setTimeout(r, 50)));
  const onScreen = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.bottom > 0 && r.top < innerHeight; };
  const pictures = [...document.querySelectorAll('#jb-root img[src]')].filter(onScreen).map(img => img.decode().catch(() => {}));
  const video = document.querySelector('#jb-background video');
  if (video && video.readyState < 3) pictures.push(new Promise(r => video.addEventListener('canplay', r, { once: true })));
  await until(PICTURES_WAIT_MS, ...pictures);
  splash.classList.add('gone');
  splash.addEventListener('transitionend', () => splash.remove(), { once: true });
  setTimeout(() => splash.remove(), 1500);
}

start().catch(e => {
  console.error(e);
  const box = document.createElement('div');
  box.id = 'jb-error';
  box.textContent = `JUKE.BOX couldn't start its screen.\n\n${e.message}`;
  document.getElementById('jb-root').replaceChildren(box);
  document.getElementById('jb-splash')?.remove();
  // Keep trying: jb-ui may simply still be starting.
  setTimeout(() => location.reload(), 5000);
});
