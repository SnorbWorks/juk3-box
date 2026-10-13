"""jb-analyzer: the reference audio analyzer.

Listens to what is playing and publishes the audio-analysis stream
(docs/contracts/audio-analysis.md, v1) -- the fast channel (the sound right
now, ~30 times a second), the slow channel (tempo, energy, mood, identity),
events (silence, sound, clip, drop) and status. It never drives anything
itself: lights, screens and logs subscribe and decide what to do.

Capture: a PipeWire monitor of the default output (what the DAC plays) via
pw-record -- a digital copy of the decoded audio, read without disturbing
playback. Identity comes from the phone's own metadata for now
(jukebox/jb-source-phone/now-playing); fingerprinting comes later.

Stages built here: 0 capture, 1 fast layer (dsp.py), 2 signal-level events
(levels.py), 3 build/drop (trajectory.py), 4 local mood (slow.py), 6 the
published stream. See docs/Audio Analyzer — Architecture.md.
"""

import asyncio
import signal
import time

import numpy as np

from shared import config, logging as jb_logging
from shared.bus import Bus
from services.jb_analyzer.dsp import FastAnalyzer
from services.jb_analyzer.levels import Levels
from services.jb_analyzer.slow import SlowAnalyzer
from services.jb_analyzer.trajectory import Trajectory

SERVICE_NAME = "jb-analyzer"
TOPIC = "jukebox/analysis/audio"
V = 1
RATE, CHANNELS, HOP = 48000, 2, 1600          # 1600 samples at 48 kHz = 30 frames a second
SLOW_EVERY_S = 2.0
RESTART_DELAY_S = 3.0


def capture_cmd(cfg: dict) -> list:
    cmd = ["pw-record", "-P", "{ stream.capture.sink=true node.name=jb-analyzer node.description=\"JUKE.BOX analyzer\" }",
           "--rate", str(RATE), "--channels", str(CHANNELS), "--format", "f32", "--latency", "30ms", "--raw"]
    if cfg.get("target"):
        cmd += ["--target", cfg["target"]]
    return cmd + ["-"]


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)

    fast = FastAnalyzer(RATE, HOP)
    fps = fast.fps
    levels, traj, slow = Levels(fps), Trajectory(fps), SlowAnalyzer(fps)
    identity = {"title": None, "artist": None, "album": None}

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        await bus.publish(f"{TOPIC}/status", {"v": V, "online": True, "module": SERVICE_NAME,
                                              "rate": RATE, "fps": round(fps, 2)}, retain=True)
        messages = await bus.subscribe("jukebox/jb-source-phone/now-playing")
        last_slow = {"v": None}

        async def publish_slow(force=False):
            state = {"v": V, "t": round(time.monotonic(), 3), **slow.state(levels.sounding),
                     "identity": dict(identity)}
            comparable = {k: v for k, v in state.items() if k != "t"}
            if force or comparable != last_slow:
                last_slow.clear()
                last_slow.update(comparable)
                await bus.publish(f"{TOPIC}/slow", state, retain=True)

        async def follow_identity():
            async for _topic, np_ in messages:
                new = {"title": np_.get("title"), "artist": np_.get("artist"), "album": np_.get("album")}
                if new["title"] and new["title"] != identity["title"]:
                    slow.reset()                       # a new song: its own tempo
                if new != identity:
                    identity.update(new)
                    await publish_slow(force=True)

        async def analyse():
            frame_bytes = HOP * CHANNELS * 4
            next_slow = time.monotonic()
            while not stop.is_set():
                proc = await asyncio.create_subprocess_exec(
                    *capture_cmd(cfg), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                log.info("capturing the output (pw-record pid %d)", proc.pid)
                try:
                    while not stop.is_set():
                        raw = await proc.stdout.readexactly(frame_bytes)
                        samples = np.frombuffer(raw, dtype=np.float32).reshape(-1, CHANNELS).mean(axis=1)
                        f = fast.process(samples)
                        now = time.monotonic()
                        events = levels.step(f["_loudness"], f["peak"])
                        build = traj.step(f["_loudness"], f["_bass_db"], levels.sounding)
                        if traj.dropped:
                            events.append("drop")
                        slow.step(f)
                        for e in events:
                            await bus.publish(f"{TOPIC}/events", {"v": V, "t": round(now, 3), "type": e})
                            if e in ("silence", "sound"):
                                log.info("%s", e)
                                await publish_slow(force=True)
                        if levels.sounding:
                            public = {k: v for k, v in f.items() if not k.startswith("_")}
                            await bus.publish(f"{TOPIC}/fast", {"v": V, "t": round(now, 3), **public, "build": build})
                        if now >= next_slow:
                            next_slow = now + SLOW_EVERY_S
                            await publish_slow()
                except asyncio.IncompleteReadError:
                    err = (await proc.stderr.read()).decode(errors="replace").strip()
                    log.warning("capture ended (%s); restarting in %.0fs", err[-300:] or "no output", RESTART_DELAY_S)
                finally:
                    if proc.returncode is None:
                        proc.terminate()
                        await proc.wait()
                if not stop.is_set():
                    await asyncio.sleep(RESTART_DELAY_S)

        tasks = [asyncio.create_task(t) for t in (analyse(), follow_identity(), bus.run_heartbeat())]
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait([stopper, *tasks], return_when=asyncio.FIRST_COMPLETED)
        failed = [t for t in done if t is not stopper and t.exception()]
        for t in tasks:
            t.cancel()
        await bus.publish(f"{TOPIC}/status", {"v": V, "online": False, "module": SERVICE_NAME}, retain=True)
        if failed:
            # A dead task takes the service down so systemd restarts it.
            raise failed[0].exception()


if __name__ == "__main__":
    asyncio.run(main())
