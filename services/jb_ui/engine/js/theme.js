// Applies a theme's manifest: styles, fonts, background and sizing.

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

  await Promise.all([
    ...(manifest.fonts || []).map(f => loadFont(f, base)),
    ...manifest.styles.map(href => loadStyle(base + href)),
  ]);
  setBackground(manifest.background, base);
  applySizing(manifest.sizing);
}

async function loadFont({ family, src, weight, stretch, style }, base) {
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
  holder.replaceChildren();
  if (!bg || bg.type === 'none') return;
  if (bg.type === 'video') {
    const v = document.createElement('video');
    Object.assign(v, { src: base + bg.src, autoplay: true, loop: true, muted: true, playsInline: true });
    holder.append(v);
  } else {
    const img = document.createElement('img');
    img.src = base + bg.src;
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
