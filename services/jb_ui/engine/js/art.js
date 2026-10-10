// Cover-art colors (docs/theme-format.md, "Art colors"). Each picture is
// sampled when it loads, so a theme's glows, gradient edges and vignettes
// match whatever image a card is showing:
//   --jb-art-1     the picture's most vivid color (glows)
//   --jb-art-2     a second vivid color, different in hue (gradient edges)
//   --jb-art-dark  the darkened average of its edges (vignettes)
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
  for (let i = 0; i < SIZE * SIZE; i++) {
    const r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
    const [h, s, v] = hsv(r, g, b);
    pixels.push({ r, g, b, h, score: s * v * (v > 0.15 ? 1 : 0) });
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
