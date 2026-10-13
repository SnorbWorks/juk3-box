"""Find each pixel's position in the mapping video (no numpy: plain Python
over small grayscale frames from ffmpeg)."""
import json, subprocess, sys

VIDEO, TIMES, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
W, H, FPS = 480, 270, 30
FRAME = W * H
log = json.load(open(TIMES))

raw = subprocess.run(["ffmpeg", "-v", "error", "-i", VIDEO, "-vf", f"scale={W}:{H},format=gray",
                      "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
frames = [raw[i:i + FRAME] for i in range(0, len(raw) - FRAME + 1, FRAME)]
means = [sum(f[::7]) / len(f[::7]) for f in frames]
print("frames", len(frames))

# The two flashes: frames well above the dark level.
dark_level = sorted(means)[len(means) // 4]
peak = max(means)
thresh = dark_level + (peak - dark_level) * 0.5
bright = [i for i, m in enumerate(means) if m > thresh]
runs, cur = [], [bright[0]]
for i in bright[1:]:
    if i == cur[-1] + 1:
        cur.append(i)
    else:
        runs.append(cur); cur = [i]
runs.append(cur)
runs = [r for r in runs if len(r) >= 10]
print("bright runs (frames):", [(r[0], r[-1]) for r in runs])
start_f, end_f = runs[0][0], runs[-1][0]
# Video seconds per Pi second, from flash-to-flash (should be ~1.0).
scale = ((end_f - start_f) / FPS) / (log["end_flash_on"] - log["flash_on"])
print(f"timing scale {scale:.4f}")

def frame_at(pi_time):
    return round(start_f + (pi_time - log["flash_on"]) * scale * FPS)

# Baseline: a dark frame between the first flash and pixel 1.
base = frames[frame_at((log["flash_off"] + log["pixels"][0]) / 2)]
points = []
for n, t in enumerate(log["pixels"], 1):
    f = frames[frame_at(t + log["step_s"] * 0.5)]
    diff = [max(0, a - b) for a, b in zip(f, base)]
    m = max(diff)
    if m < 25:
        points.append({"n": n, "x": None, "y": None, "peak": m}); continue
    cut = m * 0.6
    sx = sy = sw = 0
    for idx, d in enumerate(diff):
        if d >= cut:
            sx += (idx % W) * d; sy += (idx // W) * d; sw += d
    points.append({"n": n, "x": round(sx / sw * 4, 1), "y": round(sy / sw * 4, 1), "peak": m})
json.dump({"size": [1920, 1080], "points": points}, open(OUT, "w"))
missing = [p["n"] for p in points if p["x"] is None]
print("found", len(points) - len(missing), "of", len(points), "| not found:", missing[:30])
