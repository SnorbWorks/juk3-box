"""jb-analyzer: the reference audio analyzer.

Listens to what is playing and publishes the audio-analysis stream
(docs/contracts/audio-analysis.md, v1) -- the fast channel (the sound right
now, ~30 times a second), the slow channel (tempo, energy, mood, identity,
the song's feel), events (silence, sound, clip, drop, gaps, hits, crashes,
sections), the song's map, and status. It never drives anything itself:
lights, screens and logs subscribe and decide what to do.

Capture: a PipeWire monitor of the DAC (what the cabinet plays) via
pw-record, in stereo -- a digital copy of the decoded audio, read without
disturbing playback. Identity comes from the sources' own metadata
(jukebox/jb-source-phone/now-playing, jb-source-local for a USB drive,
jb-source-helper for the Juk3box Helper app); listeners' tags and genre from
Last.fm (tags.py) when a lastfm_key is configured.

Stages: 0 capture, 1 fast layer (dsp.py), 2 signal-level events
(levels.py), 3 build/drop (trajectory.py) and the moments a light show waits
for (moments.py), 4 local mood (slow.py) and the texture -- harmony, timbre,
the stereo picture, the key (texture.py) -- one frame through all of them in
pipeline.py; 5 the ears (ears.py: YAMNet, about once a second, in a worker
thread), tags (tags.py) and the feel (feel.py); 6 the song: live sections the
first time (sections.py), recorded (memory.py) and studied after a whole
play into its map (study.py, a niced subprocess), and on every later play
the map followed on time (sync.py) -- songs.py keeps all that; 7 the
published stream. See docs/Audio Analyzer — Architecture.md.
"""

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")     # the ears share a small CPU with everything else

import asyncio
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from shared import config, logging as jb_logging, pipewire as pw
from shared.bus import Bus
from services.jb_analyzer import ears as ears_lib
from services.jb_analyzer.memory import MIN_COVERAGE, Store, load_arrays
from services.jb_analyzer.songs import Songs
from services.jb_analyzer.tags import TagLookup, genre_of, mood_nudge

SERVICE_NAME = "jb-analyzer"
TOPIC = "jukebox/analysis/audio"
V = 1
RATE, CHANNELS, HOP = 48000, 2, 1600          # 1600 samples at 48 kHz = 30 frames a second
SLOW_EVERY_S = 2.0
RESTART_DELAY_S = 3.0
CLOCK_SLIP_S = 0.5                             # the audio's own clock this far from the system's: start it again
STUDY_NICE = 15
SOURCES = ("jukebox/jb-source-phone/now-playing", "jukebox/jb-source-local/now-playing",
           "jukebox/jb-source-helper/now-playing")


def capture_cmd(cfg: dict) -> list:
    cmd = ["pw-record", "-P", "{ stream.capture.sink=true node.dont-reconnect=true node.name=jb-analyzer node.description=\"Juk3box analyzer\" }",
           "--rate", str(RATE), "--channels", str(CHANNELS), "--format", "f32", "--latency", "30ms", "--raw"]
    target = cfg.get("target") or dac_name()
    if target:
        cmd += ["--target", target]
    return cmd + ["-"]


def dac_name() -> str | None:
    """The DAC by name, not the default output: with whole-home audio on the
    default is jb-whole-home, whose monitor is ~1.5 s ahead of what the
    cabinet plays (see services/jb_audio/outputs.py). The capture also has
    node.dont-reconnect: WirePlumber treats a stream whose target is the
    current default as following the default, and would move it."""
    try:
        dac = pw.find_dac(pw.dump_sync())
    except Exception:
        return None
    return dac["name"] if dac else None


def load_ears(log):
    """The ears (YAMNet), or None: the stream goes on without them."""
    try:
        return ears_lib.Ears(RATE)
    except Exception as e:                     # the model file missing or unreadable
        log.warning("no ears (%s): sections and the feel go without voices and instruments", e)
        return None


def load_song(store: Store, key: str) -> tuple:
    """(the song's map, its recording) from the songs folder, or (None, None)."""
    m = store.load_map(key)
    if m is None:
        return None, None
    try:
        rec = load_arrays(store.recording_path(key))
    except (OSError, ValueError, KeyError):
        rec = None                             # the map without its recording: no sync, the source's word
    return m, rec


async def main() -> None:
    log = jb_logging.setup(SERVICE_NAME)
    cfg = config.load(SERVICE_NAME)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)

    store = Store()
    ears = load_ears(log)
    songs = Songs(RATE, HOP, ears)
    pipe = songs.listening.pipe
    fps, slow, levels = pipe.fps, pipe.slow, pipe.levels
    song = {"tags": [], "genre": None}
    background = set()                                  # in-flight tasks (kept referenced)
    lookup = TagLookup(cfg.get("lastfm_key"))
    if not lookup.key:
        log.info("no lastfm_key configured; songs go untagged")
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ears")

    def spawn(coro):
        t = asyncio.create_task(coro)
        background.add(t)
        t.add_done_callback(background.discard)
        return t

    async with Bus(SERVICE_NAME, host=cfg["mqtt_host"], port=cfg["mqtt_port"]) as bus:
        log.info("connected to bus at %s:%s", cfg["mqtt_host"], cfg["mqtt_port"])
        await bus.publish(f"{TOPIC}/status", {"v": V, "online": True, "module": SERVICE_NAME,
                                              "rate": RATE, "fps": round(fps, 2), "ears": ears is not None},
                          retain=True)
        messages = await bus.subscribe(SOURCES[0])
        # USB drives (jb-source-local) and the Helper app too. Only one source
        # plays at a time; the song is whichever one last said it's playing.
        for topic in SOURCES[1:]:
            await bus.subscribe(topic)
        last_slow = {}

        def identity():
            return {k: songs.identity.get(k) for k in ("title", "artist", "album")}

        async def publish_slow(force=False):
            state = {"v": V, "t": round(time.monotonic(), 3), **slow.state(levels.sounding, estimate=False),
                     "identity": identity(), **song, **songs.slow()}
            comparable = {k: v for k, v in state.items() if k != "t"}
            if force or comparable != last_slow:
                last_slow.clear()
                last_slow.update(comparable)
                await bus.publish(f"{TOPIC}/slow", state, retain=True)

        async def publish_map():
            await bus.publish(f"{TOPIC}/map", {"v": V, "t": round(time.monotonic(), 3), **songs.map_payload()},
                              retain=True)

        async def follow_identity():
            async for topic, msg in messages:
                if not isinstance(msg, dict):
                    continue
                r = songs.now_playing(time.monotonic(), topic, msg)
                if r["finished"]:
                    spawn(after_song(r["finished"]))
                if r["changed"]:
                    song.update(tags=[], genre=None)
                    slow.nudge = 0.0
                    await publish_slow(force=True)
                    await publish_map()
                    spawn(tag_song(identity(), songs.key))
                    spawn(recall(songs.key))

        async def tag_song(which, key):
            tags = await lookup.lookup(which["artist"], which["title"])
            if tags is None or key != songs.key:
                return                                 # unknown, or the song already changed
            song.update(tags=tags, genre=genre_of(tags))
            songs.tagged(tags, song["genre"])
            slow.nudge = mood_nudge(tags)
            log.info("tags for %s - %s: %s", which["artist"], which["title"], ", ".join(tags) or "none")
            await publish_slow(force=True)

        async def recall(key):
            """A song the cabinet has learned: its map, and the sync."""
            if not key:
                return
            m, rec = await loop.run_in_executor(None, load_song, store, key)
            if m is not None and key == songs.key:
                songs.learned(key, m, rec)
                log.info("known song (%d plays): following its map%s", m.get("plays", 1),
                         "" if rec is not None else " by the source's position (no recording to sync to)")
                await publish_map()
                await publish_slow(force=True)

        async def after_song(done):
            """A song over: learn it (save the recording, study it), or count the play."""
            key = done["key"]
            if done["known"]:
                if done["coverage"] >= MIN_COVERAGE:
                    m = await loop.run_in_executor(None, store.load_map, key)
                    if m is not None:
                        m["plays"] = int(m.get("plays", 1)) + 1
                        await loop.run_in_executor(None, store.save_map, key, m)
                return
            if done["arrays"] is None:
                log.info("not learning that song: heard %d%% of it in one go", round(100 * done["coverage"]))
                return
            path = await loop.run_in_executor(None, store.save_recording, key, done["arrays"])
            proc = await asyncio.create_subprocess_exec(
                "nice", "-n", str(STUDY_NICE), sys.executable, "-m", "services.jb_analyzer.study",
                str(path), str(store.map_path(key)), stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode:
                log.warning("studying %s failed: %s", key, err.decode(errors="replace").strip()[-300:])
                return
            log.info("learned a song (%s)", key)
            if key == songs.key and songs.map is None:
                await recall(key)                      # it's playing again already

        async def listen(t, buf):
            scores = await loop.run_in_executor(pool, ears.score, buf)
            if scores is not None:
                songs.heard(t, scores)

        async def analyse():
            frame_bytes = HOP * CHANNELS * 4
            next_slow = time.monotonic()
            hearing = None
            while not stop.is_set():
                proc = await asyncio.create_subprocess_exec(
                    *capture_cmd(cfg), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                log.info("capturing the output (pw-record pid %d)", proc.pid)
                clock0, n = None, 0
                try:
                    while not stop.is_set():
                        raw = await proc.stdout.readexactly(frame_bytes)
                        now = time.monotonic()
                        # the audio's own clock (smooth), kept close to the system's
                        n += 1
                        if clock0 is None or abs(clock0 + n * HOP / RATE - now) > CLOCK_SLIP_S:
                            clock0, n = now - HOP / RATE, 1
                        t = clock0 + n * HOP / RATE
                        samples = np.frombuffer(raw, dtype=np.float32).reshape(-1, CHANNELS)
                        sections_before = len(songs.listening.sections.sections)
                        public, events = songs.frame(t, samples)
                        if ears is not None and songs.listening.ears_due(t) and (hearing is None or hearing.done()):
                            hearing = spawn(listen(t, ears.snapshot()))
                        for e in events:
                            await bus.publish(f"{TOPIC}/events", {"v": V, "t": round(now, 3), **e})
                            if e["type"] in ("silence", "sound"):
                                log.info("%s", e["type"])
                                await publish_slow(force=True)
                        if public:
                            await bus.publish(f"{TOPIC}/fast", {"v": V, "t": round(now, 3), **public})
                            if public.get("crash", 0) > 0:
                                await bus.publish(f"{TOPIC}/events", {
                                    "v": V, "t": round(now, 3), "type": "crash", "strength": public["crash"],
                                    "ago": public.get("crash_ago", 0.0)})
                        if songs.map is None and len(songs.listening.sections.sections) != sections_before:
                            await publish_map()                # the live map grew
                        if now >= next_slow:
                            next_slow = now + SLOW_EVERY_S
                            await publish_slow()
                            done = songs.tick(t)
                            if done:
                                spawn(after_song(done))
                                await publish_map()
                except asyncio.IncompleteReadError:
                    err = (await proc.stderr.read()).decode(errors="replace").strip()
                    log.warning("capture ended (%s); restarting in %.0fs", err[-300:] or "no output", RESTART_DELAY_S)
                finally:
                    if proc.returncode is None:
                        proc.terminate()
                        await proc.wait()
                if not stop.is_set():
                    await asyncio.sleep(RESTART_DELAY_S)

        await publish_map()
        tasks = [asyncio.create_task(t) for t in (analyse(), follow_identity(), bus.run_heartbeat())]
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait([stopper, *tasks], return_when=asyncio.FIRST_COMPLETED)
        failed = [t for t in done if t is not stopper and t.exception()]
        for t in tasks:
            t.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
        await bus.publish(f"{TOPIC}/status", {"v": V, "online": False, "module": SERVICE_NAME}, retain=True)
        if failed:
            # A dead task takes the service down so systemd restarts it.
            raise failed[0].exception()


if __name__ == "__main__":
    asyncio.run(main())
