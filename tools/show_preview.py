"""Watch the light show without the cabinet: replay a song's analysis
stream (tools/analyze_song.py --json) through jb-lights' real show code and
render a video of the cabinet -- upper panel, level bar, footlights and
keypad at their real positions -- with the song's audio, and a caption of
what the director is doing (style, tier, scenes, and each gap, hit, section
and drop as it happens).

    venv/bin/python -m tools.analyze_song song.m4a --json song.jsonl --quiet
    venv/bin/python -m tools.show_preview song.jsonl --audio song.m4a --out song.mp4 [--start 40 --seconds 30]

Needs ffmpeg and Pillow. Runs the same frames the service would (the
accent chain's brightness and current cap included).
"""

import argparse
import json
import subprocess

from PIL import Image, ImageDraw

from services.jb_lights import accent, buttons, matrix
from services.jb_lights.accent_lights import Looks
from services.jb_lights.audio_feed import AudioFeed, LookPicker
from services.jb_lights.levelbar import MusicLooks
from services.jb_lights.themes.accent_city import BRIGHTNESS
from services.jb_lights.themes.accent_diner import limit
from services.jb_lights.themes.buttons_music import ButtonsMusic
from services.jb_lights.themes.volume_bar_idle import Idle

W, H = 480, 860
PX_PER_IN = 18
LEFT, TOP = 42, 40
FPS = 30


def to_screen(x, y):
    return LEFT + x * accent.UPPER_WIDTH_IN * PX_PER_IN, TOP + y * accent.UPPER_WIDTH_IN / accent.UPPER_ASPECT * PX_PER_IN


def show_colour(c):
    """LEDs read brighter than their numbers: lift the low end like the eye does."""
    return tuple(int(255 * min(1.0, (v / 255) ** 0.6)) for v in c[:3])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stream")
    ap.add_argument("--audio")
    ap.add_argument("--out", default="preview.mp4")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--title", default="")
    ap.add_argument("--genre", help="stand in for Last.fm's genre (offline there are no tags)")
    ap.add_argument("--tour", help="cycle the level bar through these scenes (comma-separated), 5 s each")
    args = ap.parse_args()

    msgs = [json.loads(line) for line in open(args.stream)]
    end = msgs[-1]["t"] if args.seconds is None else args.start + args.seconds
    feed = AudioFeed(picker=LookPicker("show"))
    apos, bpos = accent.positions(), matrix.positions()
    looks, bar, keys = Looks(apos), MusicLooks(Idle()), ButtonsMusic()
    key_xy = {}
    for row, y in ((buttons.LETTERS, 0.0), (buttons.DIGITS, 1.0)):
        for k, key in enumerate(row):
            key_xy[buttons.index(key)] = (LEFT + 20 + k * 32, H - 200 + y * 30)
    key_xy[buttons.SINGLE], key_xy[buttons.ALBUM], key_xy[buttons.RESET] = (
        (LEFT + 360, H - 200), (LEFT + 360, H - 170), (LEFT + 395, H - 185))

    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"]
    if args.audio:
        cmd += ["-ss", str(args.start), "-t", str(end - args.start), "-i", args.audio, "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-shortest", args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    k, frame_n, notes = 0, 0, []
    t = 0.0
    while t < end:
        t = frame_n / FPS
        frame_n += 1
        while k < len(msgs) and msgs[k]["t"] <= t:
            m = msgs[k]
            if m["topic"].endswith("slow"):
                m["data"]["identity"] = {"title": args.title, "artist": None}
                if args.genre:
                    m["data"]["genre"], m["data"]["tags"] = args.genre, [args.genre]
            feed.on_message(m["topic"], m["data"], now=m["t"])
            if m["topic"].endswith("events") and m["data"]["type"] not in ("clip", "sound"):
                notes.append((m["t"], m["data"]["type"].upper()))
            k += 1
        snap = feed.snapshot(now=t)
        if args.tour and snap.cues:
            tour = args.tour.split(",")
            snap.cues.scene["bar"] = tour[int((t - args.start) // 5) % len(tour)]
        dt = 1 / FPS
        accent_frame = limit(looks.frame(t, dt, snap), BRIGHTNESS)
        bar_frame = bar.frame(t, dt, snap)
        key_frame = keys.frame(dt, snap) or {}
        if t < args.start:
            continue

        img = Image.new("RGB", (W, H), (8, 8, 12))
        d = ImageDraw.Draw(img)
        for (x, y), c in zip(apos, accent_frame):
            sx, sy = to_screen(x, y)
            d.ellipse((sx - 4, sy - 4, sx + 4, sy + 4), fill=show_colour(c))
        for (x, y), c in zip(bpos, bar_frame):
            sx, sy = to_screen(x, y)
            d.rectangle((sx - 2, sy - 4, sx + 2, sy + 4), fill=show_colour(c))
        for i, (sx, sy) in key_xy.items():
            r, g, b, w = key_frame.get(i, (0, 0, 0, 180))
            c = tuple(min(255, v + w) for v in (r, g, b))
            d.rounded_rectangle((sx - 11, sy - 11, sx + 11, sy + 11), radius=4, fill=show_colour(c))
        cues = snap.cues
        lines = [f"{args.title}  {int(t // 60)}:{t % 60:04.1f}"]
        if cues:
            lines.append(f"style {cues.style.name}  tier {cues.tier}  {cues.tempo:.0f} bpm  beat {cues.bar_beat + 1}")
            lines.append(f"upper {cues.scene['upper']}  foot {cues.scene['foot']}  bar {cues.scene['bar']}  keys {cues.scene['keys']}")
        else:
            lines.append(snap.look)
        recent = [n for at, n in notes if t - at < 1.0]
        if recent:
            lines.append("  ".join(recent[-3:]))
        d.text((12, 10), lines[0], fill=(200, 200, 210))
        for n, line in enumerate(lines[1:]):
            d.text((12, H - 110 + n * 16), line, fill=(255, 220, 80) if n == 2 else (200, 200, 210))
        enc.stdin.write(img.tobytes())
    enc.stdin.close()
    enc.wait()


if __name__ == "__main__":
    main()
