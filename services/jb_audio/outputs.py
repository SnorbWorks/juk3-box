"""Whole-home audio: the jukebox plays through its own DAC and an AirPlay
receiver (a JUKE Audio zone) at the same time, in sync.

How it fits together (all PipeWire, no root needed):

- An "outputs" PipeWire process, run while whole-home is on, adds two nodes:
  * jb-whole-home: a sink that becomes the default output, so the phone's
    Bluetooth stream (and drive songs) play into it. Inside it is a fixed
    delay, then the DAC. The delay is the AirPlay buffer (~1.5 s), so the
    cabinet plays at the same moment as the house.
  * the AirPlay receiver, found by name over mDNS (raop-discover), so a new
    IP address for JUKE Audio doesn't break it. Idle, it isn't connected.
- A feed (pw-loopback) from jb-whole-home's monitor -- the undelayed audio --
  to the AirPlay sink, run only while something is playing. AirPlay holds the
  zone for as long as its sink runs, and the DAC is always running (the light
  analyzer listens to it), so a permanent link would claim the house zone
  around the clock. Stopped, the sink suspends and the session closes.

The delay comes from the AirPlay protocol itself: RAOP plays at a scheduled
timestamp, so once connected the receiver's delay is a known constant, which
PipeWire's raop-sink reports as its latency (sender buffer + the receiver's
own Audio-Latency; Inside: 77175 samples at 44.1 kHz = 1.75 s). Turning
whole-home on connects silently, reads it and sets the cabinet's delay
before the jukebox switches over; it's read again each time AirPlay
connects for music, and Resync reconnects and measures afresh. The phone's
Bluetooth delay sits upstream of the split, so it moves both outputs alike.
The owner's by-ear offset is added on top. (combine-stream's own latency
compensation was tried first: it read those samples at 48 kHz, ~1.59 s,
and its links keep the AirPlay session open forever.)
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shared import files, pipewire as pw

WHOLE_HOME = "jb-whole-home"
FEED = "jb-airplay-feed"
AIRPLAY_NODE = "jb-airplay"
SETTINGS_PATH = Path("/var/lib/juk3-box/jb-audio.json")
CONF_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "jb-audio"
# Until a receiver has been measured: raop.latency.ms (1500) plus a
# typical receiver's own 250 ms.
DEFAULT_DELAY_MS = 1750
MAX_DELAY_S = 4.0
MAX_OFFSET_MS = 1000
CALIBRATE_TIMEOUT_S = 10
# A re-reading that moves the delay less than this is left alone (changing
# it mid-song is an audible jump).
RECALIBRATE_MIN_CHANGE_MS = 10
# How long after the music stops the feed stays up (a pause, the gap
# between songs); then the house zone is let go.
FEED_LINGER_S = 30
TICK_S = 5
RESTART_BACKOFF_S = (2, 5, 15, 30)

MATCH_MIN, MATCH_MAX = 0.25, 3.0

log = logging.getLogger("jb-audio.outputs")


def airplay_level(cabinet_volume: float, muted: bool, match: float) -> float:
    """The AirPlay speaker follows the cabinet's knob (and mute), scaled by
    the owner's match setting."""
    if muted:
        return 0.0
    return round(min(max(cabinet_volume * match, 0.0), 1.0), 2)


@dataclass
class Settings:
    whole_home: bool = False
    airplay: str = "Inside"          # the AirPlay receiver's name, as the phone lists it
    measured_ms: int = DEFAULT_DELAY_MS  # the receiver's delay, as last measured
    offset_ms: int = 0                # the owner's by-ear correction, added on top
    airplay_match: float = 1.0        # the AirPlay speaker's level relative to the cabinet's knob
    volume: float | None = None       # the cabinet's volume as last set, put back at startup (PipeWire
                                      # doesn't remember it for the DAC's stereo-fallback profile)
    phone: dict | None = None         # {"mac", "boot"} of the phone connected now (phone_volume.py:
                                      # a restart of jb-audio isn't a new connection)

    @property
    def delay_ms(self) -> int:
        """How far the DAC is held back."""
        return int(min(max(self.measured_ms + self.offset_ms, 0), MAX_DELAY_S * 1000))

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "Settings":
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known).clamped()

    def clamped(self) -> "Settings":
        self.measured_ms = int(min(max(self.measured_ms, 0), MAX_DELAY_S * 1000))
        self.offset_ms = int(min(max(self.offset_ms, -MAX_OFFSET_MS), MAX_OFFSET_MS))
        self.airplay_match = round(float(min(max(self.airplay_match, MATCH_MIN), MATCH_MAX)), 2)
        if not isinstance(self.phone, dict):
            self.phone = None
        if not isinstance(self.volume, (int, float)) or isinstance(self.volume, bool):
            self.volume = None
        else:
            self.volume = round(float(min(max(self.volume, 0.0), 1.0)), 3)
        return self

    def save(self, path: Path = SETTINGS_PATH) -> None:
        files.write_atomic(path, json.dumps(asdict(self), indent=2) + "\n")


# -- the PipeWire config (pure) ---------------------------------------------

_REGEX_SPECIALS = set(".^$*+?()[]{}|\\")


def spa_string(s: str) -> str:
    """A quoted SPA-JSON string."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def airplay_pattern(name: str) -> str:
    """raop-discover's service names are "<device id>@<name>"."""
    escaped = "".join("\\" + c if c in _REGEX_SPECIALS else c for c in name)
    return f"~^[0-9A-F]+@{escaped}$"


def outputs_conf(settings: Settings, dac_name: str) -> str:
    delay_s = settings.delay_ms / 1000
    return f"""# Written by jb-audio (services/jb_audio/outputs.py); regenerated on every start.
context.properties = {{ log.level = 2 }}
context.spa-libs = {{ audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }}
context.modules = [
  {{ name = libpipewire-module-rt flags = [ ifexists nofail ] }}
  {{ name = libpipewire-module-protocol-native }}
  {{ name = libpipewire-module-client-node }}
  {{ name = libpipewire-module-adapter }}
  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = {spa_string("Juk3box + " + settings.airplay)}
      media.name = {spa_string("Juk3box + " + settings.airplay)}
      filter.graph = {{
        nodes = [
          {{ type = builtin name = dl label = delay config = {{ "max-delay" = {MAX_DELAY_S} }} control = {{ "Delay (s)" = {delay_s} }} }}
          {{ type = builtin name = dr label = delay config = {{ "max-delay" = {MAX_DELAY_S} }} control = {{ "Delay (s)" = {delay_s} }} }}
        ]
        inputs = [ "dl:In" "dr:In" ]
        outputs = [ "dl:Out" "dr:Out" ]
      }}
      audio.channels = 2
      audio.position = [ FL FR ]
      capture.props = {{ node.name = "{WHOLE_HOME}" media.class = Audio/Sink }}
      playback.props = {{
        node.name = "{WHOLE_HOME}.out" node.passive = true stream.dont-remix = true
        target.object = {spa_string(dac_name)} node.dont-reconnect = true
      }}
    }}
  }}
  {{ name = libpipewire-module-raop-discover
    args = {{
      stream.rules = [
        {{ matches = [ {{ raop.name = {spa_string(airplay_pattern(settings.airplay))} }} ]
          actions = {{ create-stream = {{ node.name = "{AIRPLAY_NODE}" node.description = {spa_string("AirPlay: " + settings.airplay)} }} }} }}
      ]
    }}
  }}
]
"""


def feed_cmd() -> list[str]:
    # dont-fallback/dont-reconnect: if either end goes away the feed must
    # not be re-linked to the default sink -- that's jb-whole-home itself,
    # which would loop the audio back into its own input.
    return [
        "pw-loopback", "-n", FEED,
        "--capture-props",
        f"stream.capture.sink=true target.object={WHOLE_HOME} node.passive=true "
        "node.dont-fallback=true node.dont-reconnect=true",
        "--playback-props",
        f"target.object={AIRPLAY_NODE} stream.dont-remix=true node.dont-fallback=true node.dont-reconnect=true",
    ]


def delay_params(delay_ms: int) -> str:
    s = delay_ms / 1000
    return f'{{ params = [ "dl:Delay (s)" {s} "dr:Delay (s)" {s} ] }}'


# -- when the AirPlay feed should run (pure) --------------------------------

@dataclass
class FeedPolicy:
    """Playing anywhere -> feed on; nothing playing for FEED_LINGER_S -> off."""
    linger_s: float = FEED_LINGER_S
    playing: dict = field(default_factory=dict)   # source -> bool
    last_playing: float | None = None

    def update(self, source: str, status: str | None, now: float) -> None:
        was_playing = self.any_playing()
        self.playing[source] = status == "playing"
        if was_playing or self.any_playing():
            self.last_playing = now   # the linger counts from when the music stopped

    def any_playing(self) -> bool:
        return any(self.playing.values())

    def want_feed(self, now: float) -> bool:
        if self.any_playing():
            self.last_playing = now
            return True
        return self.last_playing is not None and now - self.last_playing < self.linger_s


def airplay_latency_ms(objects: list, name: str = AIRPLAY_NODE) -> int | None:
    """The AirPlay sink's reported latency, or None while it isn't connected
    (it reports 0 then). The latency is in samples at the sink's own rate.

    The DAC adds one graph quantum of its own and so does the feed's
    loopback on the AirPlay side; they cancel, so this is the delay."""
    for n in pw.nodes(objects):
        if n["name"] != name:
            continue
        rate = int(n["props"].get("audio.rate") or 44100)
        for o in objects:
            if o.get("id") != n["id"]:
                continue
            for lat in ((o.get("info") or {}).get("params") or {}).get("Latency") or []:
                if lat.get("direction") == "Input" and lat.get("minRate"):
                    return round(lat["minRate"] * 1000 / rate + (lat.get("minNs") or 0) / 1e6)
    return None


def default_sink_name(objects: list) -> str | None:
    """The current default sink's node name, from the pw-dump metadata."""
    for o in objects:
        if o.get("type") != "PipeWire:Interface:Metadata":
            continue
        if (o.get("props") or {}).get("metadata.name") != "default":
            continue
        for entry in o.get("metadata") or []:
            if entry.get("key") == "default.audio.sink":
                value = entry.get("value")
                return value.get("name") if isinstance(value, dict) else None
    return None


# -- running it --------------------------------------------------------------

async def _run(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=pw._env())
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} {args[1:]} failed: {err.decode().strip()}")
    return out.decode().strip()


class Outputs:
    """Owns the outputs process and the AirPlay feed; tick() keeps them right."""

    def __init__(self, settings: Settings, on_change=None):
        self.settings = settings
        self.policy = FeedPolicy()
        self.on_change = on_change          # async callback when status() changes
        self.proc: asyncio.subprocess.Process | None = None
        self.feed: asyncio.subprocess.Process | None = None
        self.failures = 0
        self.next_start = 0.0
        self.airplay_id: int | None = None
        self.cabinet: tuple[float, bool] | None = None   # the knob's (volume, muted)
        self.whole_home_id: int | None = None
        self.calibrated_for: int | None = None   # the AirPlay node id the delay was measured on
        self.calibration = "pending"   # pending | measuring | measured | estimated
        self.recheck = False           # re-read the latency once the feed has connected
        self._last_status = None
        self._lock = asyncio.Lock()   # tick() runs from the timer and from bus messages

    # what other code asks
    def status(self) -> dict:
        return {
            "enabled": self.settings.whole_home,
            "airplay": self.settings.airplay,
            "airplay_found": self.airplay_id is not None,
            "streaming": self.feed is not None and self.feed.returncode is None,
            "active": self.proc is not None and self.calibrated_for is not None,
            "calibration": self.calibration,
            "measured_ms": self.settings.measured_ms,
            "offset_ms": self.settings.offset_ms,
            "delay_ms": self.settings.delay_ms,
            "airplay_match": self.settings.airplay_match,
            "airplay_level": self._airplay_level(),
        }

    def source_status(self, source: str, status: str | None) -> bool:
        """Records a source's status; returns whether "anything playing" changed."""
        before = self.policy.any_playing()
        self.policy.update(source, status, time.monotonic())
        return self.policy.any_playing() != before

    async def set_enabled(self, enabled: bool, airplay: str | None = None) -> None:
        changed_target = airplay is not None and airplay != self.settings.airplay
        self.settings.whole_home = bool(enabled)
        if airplay:
            self.settings.airplay = airplay
        self.settings.save()
        if changed_target or not enabled:
            async with self._lock:
                await self.stop()
        self.next_start = 0.0
        await self.tick()

    async def set_offset(self, ms: int) -> None:
        self.settings.offset_ms = int(ms)
        self.settings.clamped().save()
        await self._apply_delay()
        await self._report()

    async def resync(self) -> None:
        """Drop the AirPlay connection, reconnect and measure again."""
        if not self.settings.whole_home:
            return
        log.info("resync: reconnecting %r and measuring again", self.settings.airplay)
        async with self._lock:
            await self.stop()
            self.next_start = 0.0
        await self.tick()

    async def _apply_delay(self) -> None:
        if self.whole_home_id is not None:
            await _run("pw-cli", "s", str(self.whole_home_id), "Props", delay_params(self.settings.delay_ms))
            log.info("cabinet delay now %d ms (measured %d, offset %+d)", self.settings.delay_ms,
                     self.settings.measured_ms, self.settings.offset_ms)

    def _airplay_level(self) -> float | None:
        if self.cabinet is None:
            return None
        return airplay_level(*self.cabinet, self.settings.airplay_match)

    async def follow_cabinet(self, volume: float, muted: bool) -> None:
        """The knob moved (or was read): the AirPlay speaker follows. No lock:
        this runs from status reports made while tick() holds it."""
        self.cabinet = (float(volume), bool(muted))
        await self._apply_airplay_volume()

    async def set_airplay_match(self, value: float) -> None:
        self.settings.airplay_match = float(value)
        self.settings.clamped().save()
        await self._apply_airplay_volume()
        await self._report()

    async def _apply_airplay_volume(self) -> None:
        """Checked every tick too: the speaker's level can be reset when the
        AirPlay connection is made."""
        level = self._airplay_level()
        node = self.airplay_id
        if level is None or node is None:
            return
        try:
            current = float((await _run("wpctl", "get-volume", str(node))).split()[1])
        except Exception:
            current = None
        if current is None or abs(current - level) > 0.005:
            await _run("wpctl", "set-volume", str(node), f"{level:.2f}")
            log.info("AirPlay level %.2f (cabinet %.2f%s x %.2f)", level, self.cabinet[0],
                     " muted" if self.cabinet[1] else "", self.settings.airplay_match)

    # keeping it right
    async def tick(self) -> None:
        async with self._lock:
            try:
                await self._tick()
            except Exception:
                log.exception("outputs check failed")
        await self._report()

    async def _tick(self) -> None:
        if not self.settings.whole_home:
            await self.stop()
            return
        now = time.monotonic()
        if self.proc is not None and self.proc.returncode is not None:
            log.warning("outputs process exited (%s)", self.proc.returncode)
            self.proc = None
            await self._stop_feed()
            self.failures += 1
            self.next_start = now + RESTART_BACKOFF_S[min(self.failures - 1, len(RESTART_BACKOFF_S) - 1)]
        if self.proc is None:
            if now < self.next_start:
                return
            objects = await pw.dump()
            dac = pw.find_dac(objects)
            if dac is None:
                log.warning("no DAC sink in PipeWire yet; whole-home waits")
                return
            await self._start(dac["name"])
            return
        objects = await pw.dump()
        self.whole_home_id = pw.find(objects, WHOLE_HOME)
        self.airplay_id = pw.find(objects, AIRPLAY_NODE)
        if self.whole_home_id is None:
            return  # still starting up
        self.failures = 0
        if self.airplay_id is None:
            return  # the speaker isn't on the network (yet): the DAC stays the output, undelayed
        await self._apply_airplay_volume()
        if self.calibrated_for != self.airplay_id:
            # Before taking over the output: measure, so the first song is in sync.
            await self._calibrate()
        if default_sink_name(objects) != WHOLE_HOME:
            await _run("wpctl", "set-default", str(self.whole_home_id))
            log.info("default output -> %s", WHOLE_HOME)
        want = self.policy.want_feed(now)
        feed_up = self.feed is not None and self.feed.returncode is None
        if want and not feed_up:
            await self._start_feed()
            self.recheck = True
            log.info("AirPlay feed on -> %s", self.settings.airplay)
        elif want and feed_up and self.recheck:
            measured = airplay_latency_ms(objects)
            if measured is not None:
                self.recheck = False
                if abs(measured - self.settings.measured_ms) >= RECALIBRATE_MIN_CHANGE_MS:
                    log.warning("AirPlay delay moved %d -> %d ms; correcting", self.settings.measured_ms, measured)
                    self.settings.measured_ms = measured
                    self.settings.clamped().save()
                    await self._apply_delay()
        elif not want and feed_up:
            await self._stop_feed()
            log.info("AirPlay feed off (nothing playing)")

    async def _start_feed(self) -> None:
        self.feed = await asyncio.create_subprocess_exec(
            *feed_cmd(), stdout=asyncio.subprocess.DEVNULL, env=pw._env())

    async def _calibrate(self) -> None:
        """Connect to the speaker (silence, if nothing plays), read the delay
        it reports, set the cabinet to match."""
        self.calibration = "measuring"
        await self._report()
        if self.feed is None or self.feed.returncode is not None:
            await self._start_feed()
        deadline = time.monotonic() + CALIBRATE_TIMEOUT_S
        measured = None
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            measured = airplay_latency_ms(await pw.dump())
            if measured is not None:
                break
        if measured is not None:
            self.settings.measured_ms = measured
            self.settings.clamped().save()
            self.calibration = "measured"
            log.info("measured %r: %d ms", self.settings.airplay, measured)
        else:
            self.calibration = "estimated"
            log.warning("couldn't measure %r in %ds; using %d ms", self.settings.airplay,
                        CALIBRATE_TIMEOUT_S, self.settings.measured_ms)
        await self._apply_delay()
        self.calibrated_for = self.airplay_id
        self.recheck = False

    async def _start(self, dac_name: str) -> None:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        conf = CONF_DIR / "outputs.conf"
        conf.write_text(outputs_conf(self.settings, dac_name))
        self.proc = await asyncio.create_subprocess_exec(
            "pipewire", "-c", str(conf), stdout=asyncio.subprocess.DEVNULL, env=pw._env())
        log.info("whole-home on: %s delayed %d ms + AirPlay %r", dac_name, self.settings.delay_ms,
                 self.settings.airplay)

    async def _stop_feed(self) -> None:
        if self.feed is not None and self.feed.returncode is None:
            self.feed.terminate()
            try:
                await asyncio.wait_for(self.feed.wait(), 5)
            except asyncio.TimeoutError:
                self.feed.kill()
        self.feed = None

    async def stop(self) -> None:
        await self._stop_feed()
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()
            log.info("whole-home off: the DAC is the output again")
            # WirePlumber falls back to the DAC once jb-whole-home is gone;
            # make sure, so a stale default can't leave the jukebox silent.
            try:
                dac = pw.find_dac(await pw.dump())
                if dac:
                    await _run("wpctl", "set-default", str(dac["id"]))
            except Exception:
                log.exception("couldn't set the DAC back as the default output")
        self.proc = None
        self.whole_home_id = self.airplay_id = self.calibrated_for = None
        self.calibration = "pending"

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(TICK_S)

    async def _report(self) -> None:
        s = self.status()
        if s != self._last_status:
            self._last_status = s
            if self.on_change:
                await self.on_change(s)
