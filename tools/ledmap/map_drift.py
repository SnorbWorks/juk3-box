"""LED map from a hand-held mapping video, correcting for movement.

The mapping sequence (map_sequence.py) lights every pixel dim at the start
(first flash), every 25 pixels (reference flashes) and at the end. Those
frames show every LED, so they're used to:
  1. find every LED's spot in the first frame (the "master" frame),
  2. measure how the panel moved at each reference flash (a similarity
     transform -- shift, slight turn, slight zoom -- matched to the master),
  3. undo that movement for each pixel's frame (interpolated in time), and
  4. snap each pixel to its nearest master LED, so every pixel lands on a
     real LED and two pixels can't claim the same one.

Pure Python (no numpy on the Pi); the heavy lifting is bytes.translate and
re.finditer, which run in C.

    python map_drift.py VIDEO TIMES.json OUT.json [first_flash_frame]
"""
import json
import math
import re
import subprocess
import sys

VIDEO, TIMES, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
W, H, FPS = 960, 540, 30
SCALE_OUT = 2  # back to 1920x1080 coordinates
log = json.load(open(TIMES))


def grab(t: float) -> bytes:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", VIDEO, "-frames:v", "1",
                          "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return raw[:W * H]


def bright(frame: bytes, cut: int):
    """Indices of pixels at or above cut."""
    table = bytes(1 if v >= cut else 0 for v in range(256))
    return [m.start() for m in re.finditer(b"\x01", frame.translate(table))]


def blobs(idx: list, min_px: int = 3):
    """Connected bright regions -> [(cx, cy, n_px)]."""
    todo = set(idx)
    out = []
    while todo:
        seed = todo.pop()
        stack, pts = [seed], [seed]
        while stack:
            i = stack.pop()
            x = i % W
            for j in (i - 1 if x else -1, i + 1 if x < W - 1 else -1, i - W, i + W):
                if j in todo:
                    todo.remove(j)
                    stack.append(j)
                    pts.append(j)
        if len(pts) >= min_px:
            out.append((sum(p % W for p in pts) / len(pts), sum(p // W for p in pts) / len(pts), len(pts)))
    return out


def led_spots(frame: bytes):
    """Every lit LED in an all-on frame: bright blobs of plausible size."""
    m = max(frame)
    spots = blobs(bright(frame, int(m * 0.75)))
    sizes = sorted(s[2] for s in spots)
    med = sizes[len(sizes) // 2] if sizes else 0
    return [(x, y) for x, y, n in spots if n <= med * 6]


def nearest(pts, x, y):
    best, bd = None, 1e18
    for i, (px, py) in enumerate(pts):
        d = (px - x) ** 2 + (py - y) ** 2
        if d < bd:
            best, bd = i, d
    return best, math.sqrt(bd)


def fit_similarity(src, dst):
    """Least-squares a, b, tx, ty with dst = [a -b; b a] src + t."""
    n = len(src)
    sx = sum(p[0] for p in src) / n; sy = sum(p[1] for p in src) / n
    dx = sum(p[0] for p in dst) / n; dy = sum(p[1] for p in dst) / n
    num_a = num_b = den = 0.0
    for (x, y), (u, v) in zip(src, dst):
        x -= sx; y -= sy; u -= dx; v -= dy
        num_a += x * u + y * v
        num_b += x * v - y * u
        den += x * x + y * y
    a, b = num_a / den, num_b / den
    return a, b, dx - (a * sx - b * sy), dy - (b * sx + a * sy)


def apply(T, x, y):
    a, b, tx, ty = T
    return a * x - b * y + tx, b * x + a * y + ty


def invert(T):
    a, b, tx, ty = T
    d = a * a + b * b
    ia, ib = a / d, -b / d
    return ia, ib, -(ia * tx - ib * ty), -(ib * tx + ia * ty)


def register(master, spots):
    """Transform taking master coordinates to this frame's: coarse shift
    search (most spots within 6 px), then a few rounds of match-and-fit."""
    grid = {}
    for (x, y) in spots:
        grid.setdefault((int(x // 8), int(y // 8)), []).append((x, y))

    def matches(T, tol):
        pairs = []
        for (x, y) in master:
            u, v = apply(T, x, y)
            gx, gy = int(u // 8), int(v // 8)
            best, bd = None, tol * tol
            for cx in (gx - 1, gx, gx + 1):
                for cy in (gy - 1, gy, gy + 1):
                    for (p, q) in grid.get((cx, cy), ()):
                        d = (p - u) ** 2 + (q - v) ** 2
                        if d < bd:
                            best, bd = (p, q), d
            if best:
                pairs.append(((x, y), best))
        return pairs

    best_T, best_n = (1.0, 0.0, 0.0, 0.0), -1
    for dx in range(-60, 61, 3):
        for dy in range(-60, 61, 3):
            T = (1.0, 0.0, float(dx), float(dy))
            n = len(matches(T, 6))
            if n > best_n:
                best_T, best_n = T, n
    T = best_T
    for tol in (12, 8, 6, 5):
        pairs = matches(T, tol)
        if len(pairs) >= 10:
            T = fit_similarity([p for p, _ in pairs], [q for _, q in pairs])
    return T, len(matches(T, 5))


# 1. Master frame: early in the first flash, before the phone's exposure moved.
first_f = int(sys.argv[4]) if len(sys.argv) > 4 else 20
master_t = first_f / FPS
master = led_spots(grab(master_t))
print(f"master frame {first_f}: {len(master)} LED spots")

# Video time of a Pi timestamp: the first flash started at frame `start`.
start = first_f - 6  # the bright run began ~6 frames before the master frame
def vt(pi_time):
    return start / FPS + (pi_time - log["flash_on"])

# 2. Movement at each reference flash.
refs = [(master_t, (1.0, 0.0, 0.0, 0.0))]
for t_pi in log.get("refs", []) + [log["end_flash_on"]]:
    t = vt(t_pi + 0.25)
    spots = led_spots(grab(t))
    T, n = register(master, spots)
    a, b, tx, ty = T
    print(f"ref at {t:6.2f}s: {len(spots)} spots, {n} matched, shift ({tx:+.1f},{ty:+.1f}) "
          f"turn {math.degrees(math.atan2(b, a)):+.2f} deg, zoom {math.hypot(a, b):.3f}")
    refs.append((t, T))


def movement(t):
    """Interpolated master->frame transform at video time t."""
    for (t0, A), (t1, B) in zip(refs, refs[1:]):
        if t <= t1:
            k = 0 if t1 == t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
            return tuple(p + (q - p) * k for p, q in zip(A, B))
    return refs[-1][1]


# 3-4. Each pixel: which known LED lit up? Only the master LED spots are
# checked (moved to where the panel is at that moment), each by how much it
# brightened over a dark frame -- reflections on the table and screens in the
# background aren't at LED spots, so they can't win (they did, when the
# whole picture was searched).
dark = grab(vt(log["flash_off"]) + 1.0)

def spot_level(frame, x, y):
    xi, yi = int(round(x)), int(round(y))
    best = 0
    for yy in range(max(0, yi - 2), min(H, yi + 3)):
        for xx in range(max(0, xi - 2), min(W, xi + 3)):
            best = max(best, frame[yy * W + xx] - dark[yy * W + xx])
    return best

points, claimed = [], {}
for n, t_pi in enumerate(log["pixels"], 1):
    t = vt(t_pi + log["step_s"] * 0.5)
    f = grab(t)
    T = movement(t)
    levels = sorted(((spot_level(f, *apply(T, x, y)), i) for i, (x, y) in enumerate(master)), reverse=True)
    (top, i), (second, _) = levels[0], levels[1]
    sx, sy = master[i]
    claimed.setdefault(i, []).append(n)
    points.append({"n": n, "x": round(sx * SCALE_OUT, 1), "y": round(sy * SCALE_OUT, 1),
                   "level": top, "runner_up": second})

json.dump({"size": [1920, 1080], "master_time": master_t, "points": points}, open(OUT, "w"))
clashes = {i: ns for i, ns in claimed.items() if len(ns) > 1}
far = [(p["n"], p["level"], p["runner_up"]) for p in points if p["level"] < 2 * max(p["runner_up"], 1)]
print(f"pixels mapped: {len(points)}; LEDs used: {len(claimed)} of {len(master)}")
print("two pixels on one LED:", sorted(clashes.values())[:20])
print("unsure (lit spot not clearly brightest):", far[:20])
