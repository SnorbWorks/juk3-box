"""Run a song file through jb-analyzer's pipeline, offline, and print what
the cabinet would hear: the moments (gaps, hits, sections, drops), the
build, the tempo, and the drums second by second. For tuning the analyzer
against real music without playing it.

    venv/bin/python -m tools.analyze_song song.m4a [--json out.jsonl] [--quiet]

--json writes the stream as the lights would receive it (one message per
line: {"t", "topic", "data"}), to replay through jb-lights' show offline.
Needs ffmpeg to decode.
"""

import argparse
import json
import subprocess
import sys

import numpy as np

from services.jb_analyzer.pipeline import Pipeline

RATE, HOP = 48000, 1600
TOPIC = "jukebox/analysis/audio"


def decode(path):
    from tools.learn_song import decode as decode_stereo      # ffmpeg, or afconvert on a Mac
    return decode_stereo(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--json")
    ap.add_argument("--quiet", action="store_true", help="moments only, no per-second lines")
    args = ap.parse_args()

    audio = decode(args.path)
    pipe = Pipeline(RATE, HOP)
    fps = pipe.fps
    out = open(args.json, "w") if args.json else None
    sec = {"beat": 0, "kick": 0, "snare": 0, "hat": 0, "level": 0.0, "n": 0, "build": "none"}
    counts = {}
    for i, start in enumerate(range(0, audio.size - HOP + 1, HOP)):
        t = i / fps
        public, events = pipe.frame(audio[start:start + HOP])
        for e in events:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
            extra = " ".join(f"{k}={v}" for k, v in e.items() if k != "type")
            print(f"{int(t // 60)}:{t % 60:05.2f}  ** {e['type'].upper()} {extra}")
            if out:
                out.write(json.dumps({"t": round(t, 3), "topic": f"{TOPIC}/events", "data": {"v": 1, **e}}) + "\n")
        if public:
            if out:
                out.write(json.dumps({"t": round(t, 3), "topic": f"{TOPIC}/fast", "data": {"v": 1, **public}}) + "\n")
            sec["beat"] += public["beat"]
            for d in ("kick", "snare", "hat"):
                sec[d] += public[d] >= 0.5
            sec["level"] += public["level"]
            sec["n"] += 1
            if public["build"] != "none":
                sec["build"] = public["build"]
        if (i + 1) % round(fps * 2) == 0:
            slow = pipe.slow.state(pipe.levels.sounding, estimate=False)
            if out:
                out.write(json.dumps({"t": round(t, 3), "topic": f"{TOPIC}/slow", "data": {"v": 1, **slow}}) + "\n")
        if (i + 1) % round(fps) == 0 and not args.quiet:
            n = max(sec["n"], 1)
            level = sec["level"] / n
            print(f"{int(t // 60)}:{t % 60:05.2f}  {'#' * round(level * 20):20s} beats {sec['beat']} "
                  f"k{sec['kick']} s{sec['snare']} h{sec['hat']}  tempo {pipe.slow.tempo or 0:5.1f}  {sec['build']}")
            sec = {"beat": 0, "kick": 0, "snare": 0, "hat": 0, "level": 0.0, "n": 0, "build": "none"}
    print("totals:", counts, file=sys.stderr)
    if out:
        out.close()


if __name__ == "__main__":
    main()
