// JUKE.BOX UI engine: loads the active theme and builds the screen from
// its layouts. Behavior lives here; the look lives in the theme.

import { albumSongs, openFilter, pick, setSort, upLevel } from './board.js';
import { connectBus, publish } from './bus.js';
import { startFeatured } from './featured.js';
import { click, currentMode, currentStopKey, enterMode, focusKey, goToRest, refocus, rotate } from './focus.js';
import { renderLayout } from './layout.js';
import { nowPlayingData, playingLibraryTrack } from './np.js';
import { closePopup, initPopups, openPopup, popupOpen, updatePopup } from './popup.js';
import { sectionView } from './sections.js';
import { store } from './store.js';
import { fetchTheme, applyTheme } from './theme.js';
import { sized } from './tracks.js';

const VOLUME_STEP = 0.05;   // per knob detent
const NOTICE_MS = 4000;
const IDLE_CHECK_MS = 10000;
const OVERLAY_TICK_MS = 500;

// Used when a theme has no layout for the See All view.
const DEFAULT_COLLECTION_LAYOUT = {
  class: 'jb-collection-screen',
  children: [{ block: 'header' }, { block: 'collection', focus: 'collection' }],
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
    settings: theme.settings,
    actions,
  };
}

function showScreen(name, { remember = true } = {}) {
  const holder = document.querySelector('jb-screen');
  if (!holder) return;
  const from = store.get('screen');
  if (name === 'home') history.length = 0;
  else if (remember && from && from !== name) history.push(from);
  const layout = theme.layouts[name] || (name === 'collection' ? DEFAULT_COLLECTION_LAYOUT : null);
  if (layout) {
    holder.replaceChildren(renderLayout(layout, context()));
  } else {
    // The theme has no layout for this screen (only home and all-songs
    // are required). The engine's own version comes later; for now, say so.
    const missing = document.createElement('div');
    missing.dataset.placeholder = name;
    holder.replaceChildren(missing);
  }
  html.dataset.screen = name;
  store.set('screen', name);
  refocus();
}

function setVolumeVar(level) {
  html.style.setProperty('--jb-volume', level.toFixed(3));
}

// Turning the knob changes the volume until a click or a few idle seconds.
function volumeMode() {
  let level = store.get('volume')?.volume ?? 0;
  const show = () => {
    setVolumeVar(level);
    updatePopup('volume', { title: 'Volume', percent: Math.round(level * 100) });
  };
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

// The phone badge. Managing phones (connect, switch, forget) comes with
// the phone connection manager; for now it shows who's connected.
function phones() {
  const p = store.get('phone');
  openPopup('phones', {
    title: 'Phone',
    name: p?.connected ? p.name : 'No phone connected',
    status: p?.connected ? 'Connected over Bluetooth' : 'Connect your phone in its Bluetooth settings.',
  });
  setTimeout(() => closePopup('phones'), NOTICE_MS);
}

function watchSystem() {
  store.watch(html, 'phone', p => { html.dataset.phone = p?.connected ? 'connected' : 'disconnected'; });
  // Lets themes animate things (the EQ icon) while a song plays.
  store.watch(html, 'nowPlaying', np => html.toggleAttribute('data-playing', np?.status === 'playing'));

  // While the knob is setting the volume, its own running total is what's
  // shown: a confirmation for an older step can arrive after newer steps
  // and would snap the display backwards.
  store.watch(html, 'volume', v => {
    if (typeof v?.volume === 'number' && currentMode() !== 'volume') setVolumeVar(v.volume);
  });

  // A notice from jb-core shows briefly, unless the knob is busy.
  store.watch(html, 'notice', n => {
    if (n?.type !== 'connect_device' || currentMode() !== 'select' || popupOpen()) return;
    openPopup('notice', { title: 'Connect your phone', message: 'Connect your phone over Bluetooth to play music.' });
    setTimeout(() => closePopup('notice'), NOTICE_MS);
  });

  // A pick the phone couldn't play. Usually another app (an audiobook, a
  // video) has the phone's audio: the jukebox can only browse and play
  // from the Music app.
  store.watch(html, 'playError', e => {
    if (!e || currentMode() !== 'select' || popupOpen()) return;
    openPopup('notice', {
      title: 'Couldn\'t play that song',
      message: 'Open the Music app on your phone, then pick it again.',
    });
    setTimeout(() => closePopup('notice'), NOTICE_MS + 2000);
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
    if (!minutes || (store.get('screen') === 'home' && !popupOpen())) return;
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
  theme = await fetchTheme();
  await applyTheme(theme);
  store.set('settings', theme.settings);
  initPopups(theme.blocks);
  watchSystem();
  watchPower();
  watchAlbumPlay();
  watchIdle();
  startFeatured();
  document.getElementById('jb-root').replaceChildren(renderLayout(theme.layouts.frame, context()));
  showScreen('home');
  goToRest();
  watchKeyboard();
  connectBus();
}

start().catch(e => {
  console.error(e);
  const box = document.createElement('div');
  box.id = 'jb-error';
  box.textContent = `JUKE.BOX couldn't start its screen.\n\n${e.message}`;
  document.getElementById('jb-root').replaceChildren(box);
  // Keep trying: jb-ui may simply still be starting.
  setTimeout(() => location.reload(), 5000);
});
