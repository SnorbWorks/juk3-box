// Cover-art colors (docs/theme-format.md, "Art colors"). Each picture is
// sampled when it loads, so a theme's glows, gradient edges and vignettes
// match whatever image a card is showing:
//   --jb-art-1     the picture's most vivid color (glows)
//   --jb-art-2     a second vivid color, different in hue (gradient edges)
//   --jb-art-dark  the darkened average of its edges (vignettes)
//   --jb-art-main  its dominant color (the hue covering most of it), at a
//                  mid-light tone, so any cover gives a usable surface
// They're set on the closest [data-art-scope] around the image, or the
// image's parent.

const SIZE = 24;
const cache = new Map();   // image URL -> colors
let canvas = null;

export function watchArt(img) {
  if (img.jbArt) return;
  img.jbArt = true;
  img.addEventListener('load', () => apply(img));
  if (img.complete && img.naturalWidth) apply(img);
}

function apply(img) {
  const colors = colorsOf(img);
  if (!colors) return;
  const scope = img.closest('[data-art-scope]') || img.parentElement;
  if (!scope) return;
  scope.style.setProperty('--jb-art-1', colors.one);
  scope.style.setProperty('--jb-art-2', colors.two);
  scope.style.setProperty('--jb-art-dark', colors.dark);
  scope.style.setProperty('--jb-art-main', colors.main);
}

function colorsOf(img) {
  const url = img.currentSrc || img.src;
  if (cache.has(url)) return cache.get(url);
  let data;
  try {
    canvas ??= Object.assign(document.createElement('canvas'), { width: SIZE, height: SIZE });
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, SIZE, SIZE);
    data = ctx.getImageData(0, 0, SIZE, SIZE).data;
  } catch {
    return null;  // a picture the browser won't let us read (no CORS)
  }

  const pixels = [];
  let er = 0, eg = 0, eb = 0, en = 0;
  let ar = 0, ag = 0, ab = 0;
  // Hue buckets of 30 degrees, weighted by saturation: the fullest is the
  // dominant color. Greys and near-blacks don't vote.
  const buckets = Array.from({ length: 12 }, () => ({ w: 0, r: 0, g: 0, b: 0 }));
  for (let i = 0; i < SIZE * SIZE; i++) {
    const r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
    const [h, s, v] = hsv(r, g, b);
    pixels.push({ r, g, b, h, score: s * v * (v > 0.15 ? 1 : 0) });
    ar += r; ag += g; ab += b;
    if (s > 0.1 && v > 0.12) {
      const k = buckets[Math.floor(h / 30) % 12];
      k.w += s; k.r += r * s; k.g += g * s; k.b += b * s;
    }
    const x = i % SIZE, y = Math.floor(i / SIZE);
    if (x < 3 || y < 3 || x >= SIZE - 3 || y >= SIZE - 3) {
      er += r; eg += g; eb += b; en++;
    }
  }
  pixels.sort((a, b) => b.score - a.score);
  const one = pixels[0];
  // The next strong color at least 50 degrees of hue away; else a lighter
  // version of the first.
  const two = pixels.slice(0, 160).find(p => p.score > 0.12 && hueGap(p.h, one.h) > 50);
  const lift = c => Math.min(255, Math.round(c * 1.25 + 30));
  const colors = {
    one: rgb(boost(one)),
    two: two ? rgb(boost(two)) : `rgb(${lift(one.r)}, ${lift(one.g)}, ${lift(one.b)})`,
    dark: `rgb(${Math.round(er / en * 0.35)}, ${Math.round(eg / en * 0.35)}, ${Math.round(eb / en * 0.35)})`,
    main: mainColor(buckets, ar / (SIZE * SIZE), ag / (SIZE * SIZE), ab / (SIZE * SIZE)),
  };
  cache.set(url, colors);
  return colors;
}

// Glows read best a little brighter and more saturated than the raw pixel.
function boost({ r, g, b }) {
  const max = Math.max(r, g, b, 1);
  const k = Math.min(2.2, 235 / max);
  return { r: Math.round(r * k), g: Math.round(g * k), b: Math.round(b * k) };
}

// The dominant hue's average color (or, for a cover with almost no color,
// its overall average), brought to a mid-light tone: lightness 0.5-0.62,
// saturation lifted a little (muted covers still read as a color) to 0.3-0.7.
function mainColor(buckets, r0, g0, b0) {
  const top = buckets.reduce((a, k) => (k.w > a.w ? k : a));
  const colorful = top.w > SIZE * SIZE * 0.015;
  const [h, s, l] = hsl(colorful ? top.r / top.w : r0, colorful ? top.g / top.w : g0, colorful ? top.b / top.w : b0);
  const sat = colorful ? Math.min(0.7, Math.max(0.3, s * 1.4)) : Math.min(0.12, s);
  return `hsl(${Math.round(h)} ${Math.round(sat * 100)}% ${Math.round(Math.min(0.62, Math.max(0.5, l)) * 100)}%)`;
}

function hsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, d = max - min;
  if (!d) return [0, 0, l];
  const s = d / (1 - Math.abs(2 * l - 1));
  let h;
  if (max === r) h = 60 * (((g - b) / d) % 6);
  else if (max === g) h = 60 * ((b - r) / d + 2);
  else h = 60 * ((r - g) / d + 4);
  return [(h + 360) % 360, s, l];
}

function rgb({ r, g, b }) {
  return `rgb(${r}, ${g}, ${b})`;
}

function hsv(r, g, b) {
  const max = Math.max(r, g, b) / 255, min = Math.min(r, g, b) / 255, d = max - min;
  let h = 0;
  if (d) {
    if (max === r / 255) h = 60 * (((g - b) / 255 / d) % 6);
    else if (max === g / 255) h = 60 * ((b - r) / 255 / d + 2);
    else h = 60 * ((r - g) / 255 / d + 4);
  }
  return [(h + 360) % 360, max ? d / max : 0, max];
}

function hueGap(a, b) {
  const d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}
