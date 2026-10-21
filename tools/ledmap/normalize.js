const fs = require("fs");
const raw = JSON.parse(fs.readFileSync(process.argv[2]));
const pts = raw.points;
const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
const w = x1 - x0, h = y1 - y0;
const lines = pts.map(p => `  [${p.n}, ${((p.x - x0) / w).toFixed(4)}, ${((p.y - y0) / h).toFixed(4)}]`);
const out = `{
 "about": "Juk3box upper panel LED map: [pixel n (1-189, chain order), x, y]. x 0..1 left to right and y 0..1 top to bottom, across the LEDs' own extent; aspect = that extent's width / height. Camera-mapped 2026-10-12 with tools/ledmap.",
 "aspect": ${(w / h).toFixed(4)},
 "points": [
${lines.join(",\n")}
 ]
}
`;
fs.writeFileSync(process.argv[3], out);
console.log("aspect", (w / h).toFixed(3), "points", pts.length);
