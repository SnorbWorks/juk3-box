"""Learn a song file the way the cabinet learns one it plays: listen to it
frame by frame (the pipeline, the texture, the ears), keep the recording,
then study it (services/jb_analyzer/study.py) and print its map.

    venv/bin/python -m tools.learn_song song.m4a [--map out.json] [--npz rec.npz] [--picture ssm.png]
    venv/bin/python -m tools.learn_song --study rec.npz        (re-study a recording, e.g. one from the cabinet)

Decodes with ffmpeg, or on a Mac with afconvert.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
import zlib

import numpy as np

from services.jb_analyzer import ears, study
from services.jb_analyzer.listening import Listening
from services.jb_analyzer.memory import load_arrays

RATE, HOP = 48000, 1600


def decode(path) -> np.ndarray:
    """(n, 2) float32 at 48 kHz."""
    if shutil.which("ffmpeg"):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "song.wav")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "2", path, out], check=True)
        w = wave.open(out)
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        return x.reshape(-1, w.getnchannels())


def listen(path, title=None, artist=None) -> dict:
    audio = decode(path)
    duration = audio.shape[0] / RATE
    li = Listening(RATE, HOP, ears.Ears(RATE))
    li.new_song({"title": title or os.path.basename(path), "artist": artist, "duration": duration}, 0.0)
    t0 = time.perf_counter()
    for i, start in enumerate(range(0, audio.shape[0] - HOP + 1, HOP)):
        t = (start + HOP) / RATE
        li.frame(audio[start:start + HOP], t)
        if i % 60 == 0:
            li.position(t, t)
        if li.ears_due(t):
            li.heard(t, li.ears.listen())
    print(f"listened to {duration:.0f} s in {time.perf_counter() - t0:.1f} s", file=sys.stderr)
    return li.recording.arrays()


def clock(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def show(m):
    print(f"{m['song'].get('title')}  tempo {m['tempo']}  key {m['key']}  beats {len(m['beats'])}"
          f"  bars {len(m['downbeats'])}  confidence {m['confidence']}")
    print("  ears", {k: v for k, v in m["ears"].items() if v >= 0.005}, " genres", m["genres"])
    for s in m["sections"]:
        tags = " LAST" if s.get("last") else ""
        tags += " PEAK" if s.get("peak") else ""
        print(f"  {clock(s['start']):>7} - {clock(s['end']):>7}  {s['bars']:>3} bars  {s['label']}{s['n']}  "
              f"{s['role']:<10} energy {s['energy']:.2f}  voice {s['voice']:.2f}  drums {s['drums']:.2f}  "
              f"ring {s['ring']:.2f}{tags}")
    kinds = {}
    for e in m["moments"]:
        kinds.setdefault(e["type"], []).append(e)
    for k, es in kinds.items():
        print(f"  {k}: {len(es)}", " ".join(clock(e["t"]) for e in es[:24]))


def png(path, matrix):
    """A grey picture of a 0..1 matrix (no imaging library needed)."""
    m = np.clip(matrix, 0, 1)
    m = (255 * (m - m.min()) / (m.max() - m.min() + 1e-9)).astype(np.uint8)
    scale = max(1, 600 // m.shape[0])
    m = np.kron(m, np.ones((scale, scale), dtype=np.uint8))
    h, w = m.shape
    raw = b"".join(b"\x00" + m[y].tobytes() for y in range(h))
    def chunk(tag, data):
        return len(data).to_bytes(4, "big") + tag + data + zlib.crc32(tag + data).to_bytes(4, "big")
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x00\x00\x00\x00")
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--study", help="a recording (.npz) to study instead of a song file")
    ap.add_argument("--title")
    ap.add_argument("--artist")
    ap.add_argument("--map")
    ap.add_argument("--npz")
    ap.add_argument("--picture", help="write the bar similarity matrix as a PNG")
    a = ap.parse_args()
    if a.study:
        rec = load_arrays(a.study)
    else:
        rec = listen(a.path, a.title, a.artist)
        if a.npz:
            np.savez_compressed(a.npz, **rec)
            rec = load_arrays(a.npz)
        else:
            for k in ("genres", "events", "identity"):
                rec[k] = json.loads(str(rec[k]))
            rec["fps"] = float(rec["fps"])
    t0 = time.perf_counter()
    m = study.study(rec)
    print(f"studied in {time.perf_counter() - t0:.2f} s", file=sys.stderr)
    show(m)
    if a.map:
        with open(a.map, "w") as f:
            json.dump(m, f)
    if a.picture and m["downbeats"]:
        o = study.onset_envelope(rec["fast"])
        bpm, _ = study.tempo_of(o, rec["fps"])
        idx = study.track_beats(o, 60 * rec["fps"] / bpm)
        beat_t = study.at(study.refine(o, idx), rec["fast_pos"])
        phase = study.downbeat_phase(beat_t, rec["fast_pos"], rec["fast"], rec["pos"],
                                     rec["chroma"].astype(np.float32), rec["events"])
        bars = list(range(phase, beat_t.size - 1, study.BEATS_PER_BAR))
        f = study.bar_features(bars, beat_t, rec)
        png(a.picture, study.ssm(f)["all"])


if __name__ == "__main__":
    main()
