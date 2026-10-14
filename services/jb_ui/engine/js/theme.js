// Applies a theme's manifest: styles, fonts, background and sizing, and
// its options with the owner's choices (colors, fonts...).

import { PREVIEW } from './bus.js';

export async function fetchTheme() {
  // The admin preview passes its own query (which theme, drafts) through.
  const res = await fetch('/api/theme' + location.search);
  if (!res.ok) throw new Error(`/api/theme returned ${res.status}`);
  return res.json();
}

export async function applyTheme(theme) {
  const { manifest, base } = theme;
  const root = document.documentElement;
  root.dataset.themeId = theme.id;
  root.dataset.themeName = manifest.name;
  if (theme.fallback) {
    root.dataset.themeFallback = theme.fallback.wanted;
    console.warn(`theme "${theme.fallback.wanted}" can't load, using "${theme.id}":`, theme.fallback.problems);
  }
  for (const w of theme.warnings) console.warn(`theme ${theme.id}: ${w}`);

  const options = applyOptions(theme, theme.customize?.values || {});
  await Promise.all([
    ...(manifest.fonts || []).map(f => loadFont(f, base)),
    ...manifest.styles.map(href => loadStyle(base + href)),
    options,
  ]);
  applySizing(manifest.sizing);
  // The admin's Customize page shows changes here as they're made.
  if (PREVIEW) {
    addEventListener('message', e => {
      if (e.origin === location.origin && e.data?.type === 'jb-customize') applyOptions(theme, e.data.values || {});
    });
  }
}

// The theme's options (docs/theme-format.md, "Customize"): each becomes
// --opt-<id> on the page; an on/off one also data-opt-<id>="on|off"; a
// font loads from the engine's library; a background picture replaces the
// theme's background.
export async function applyOptions(theme, values) {
  const { manifest, base } = theme;
  const root = document.documentElement;
  let background = manifest.background;
  const loads = [];
  for (const o of manifest.customize?.options || []) {
    const v = values[o.id] ?? o.default;
    const name = `--opt-${o.id}`;
    if (o.type === 'color' || o.type === 'range') {
      root.style.setProperty(name, String(v));
    } else if (o.type === 'toggle') {
      root.style.setProperty(name, v ? '1' : '0');
      root.setAttribute(`data-opt-${o.id}`, v ? 'on' : 'off');
    } else if (o.type === 'font') {
      const f = v !== 'theme' && (theme.fonts || []).find(x => x.id === v);
      if (f) loads.push(loadFont(f, ''));
      root.style.setProperty(name, f ? `"${f.family}", ${f.fallback || 'sans-serif'}` : (o.family || 'sans-serif'));
    } else if (o.type === 'picture') {
      const url = !v ? (o.default ? assetUrl(o.default, base) : '') : assetUrl(v, base);
      root.style.setProperty(name, url ? `url("${url}")` : 'none');
      if (o.role === 'background') background = url ? { type: 'image', url } : manifest.background;
    }
  }
  setBackground(background, base);
  await Promise.all(loads);
}

function assetUrl(src, base) {
  return String(src).startsWith('/media/') ? String(src) : base + src;
}

const loadedFonts = new Set();

async function loadFont({ family, src, weight, stretch, style }, base) {
  if (loadedFonts.has(base + src)) return;
  loadedFonts.add(base + src);
  try {
    // A variable font declares its ranges, e.g. weight "100 1000" and
    // stretch "25% 151%", so bold and condensed use its real axes.
    const descriptors = Object.fromEntries(Object.entries({ weight, stretch, style }).filter(([, v]) => v));
    const face = new FontFace(family, `url("${base}${src}")`, descriptors);
    document.fonts.add(await face.load());
  } catch (e) {
    console.warn(`font ${family} didn't load:`, e);
  }
}

function loadStyle(href) {
  return new Promise(resolve => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    // A broken stylesheet shouldn't stop the jukebox from starting.
    link.onload = link.onerror = resolve;
    document.head.append(link);
  });
}

function setBackground(bg, base) {
  const holder = document.getElementById('jb-background');
  const url = bg && bg.type !== 'none' ? bg.url || base + bg.src : '';
  // Unchanged: keep it (a live preview change mustn't restart a video).
  if (holder.dataset.src === url) return;
  holder.dataset.src = url;
  holder.replaceChildren();
  if (!url) return;
  if (bg.type === 'video') {
    const v = document.createElement('video');
    Object.assign(v, { src: url, autoplay: true, loop: true, muted: true, playsInline: true });
    holder.append(v);
  } else {
    const img = document.createElement('img');
    img.src = url;
    img.alt = '';
    holder.append(img);
  }
}

// Canvas sizing: the theme is designed at one exact size, and the whole
// screen is scaled evenly to fit, centered. Fluid themes size themselves.
function applySizing(sizing) {
  const root = document.getElementById('jb-root');
  if (!sizing || sizing.mode !== 'canvas') return;
  const { width, height } = sizing;
  Object.assign(root.style, {
    position: 'absolute', width: `${width}px`, height: `${height}px`,
    left: '50%', top: '50%', transformOrigin: '0 0',
  });
  const fit = () => {
    const scale = Math.min(innerWidth / width, innerHeight / height);
    root.style.transform = `scale(${scale}) translate(-50%, -50%)`;
  };
  fit();
  addEventListener('resize', fit);
}
