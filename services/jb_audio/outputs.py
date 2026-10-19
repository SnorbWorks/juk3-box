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

The delay is fixed rather than measured (combine-stream's latency
compensation measures it, but its links keep the AirPlay session open
forever), so starting and stopping the feed never jumps the cabinet audio.
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
# The AirPlay sender's buffer is raop.latency.ms (1500 by default); the
# rest is the receiver's output and the network. 1580 is what PipeWire's
# own latency compensation measured against JUKE Audio (2026-10-18).
DEFAULT_DELAY_MS = 1580
MAX_DELAY_S = 4.0
# How long after the music stops the feed stays up (a pause, the gap
# between songs); then the house zone is let go.
FEED_LINGER_S = 30
TICK_S = 5
RESTART_BACKOFF_S = (2, 5, 15, 30)

log = logging.getLogger("jb-audio.outputs")


@dataclass
class Settings:
    whole_home: bool = False
    airplay: str = "Inside"          # the AirPlay receiver's name, as the phone lists it
    delay_ms: int = DEFAULT_DELAY_MS  # how far the DAC is held back
    airplay_volume: float = 0.7       # the receiver's own level (its zone volume still applies)

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "Settings":
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known).clamped()

    def clamped(self) -> "Settings":
        self.delay_ms = int(min(max(self.delay_ms, 0), MAX_DELAY_S * 1000))
        self.airplay_volume = float(min(max(self.airplay_volume, 0.0), 1.0))
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
      node.description = {spa_string("JUKE.BOX + " + settings.airplay)}
      media.name = {spa_string("JUKE.BOX + " + settings.airplay)}
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
        self.airplay_volume_set_on: int | None = None
        self.whole_home_id: int | None = None
        self._last_status = None
        self._lock = asyncio.Lock()   # tick() runs from the timer and from bus messages

    # what other code asks
    def status(self) -> dict:
        return {
            "enabled": self.settings.whole_home,
            "airplay": self.settings.airplay,
            "airplay_found": self.airplay_id is not None,
            "streaming": self.feed is not None and self.feed.returncode is None,
            "delay_ms": self.settings.delay_ms,
            "airplay_volume": self.settings.airplay_volume,
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

    async def set_delay(self, ms: int) -> None:
        self.settings.delay_ms = int(ms)
        self.settings.clamped().save()
        if self.whole_home_id is not None:
            await _run("pw-cli", "s", str(self.whole_home_id), "Props", delay_params(self.settings.delay_ms))
            log.info("cabinet delay now %d ms", self.settings.delay_ms)
        await self._report()

    async def set_airplay_volume(self, value: float) -> None:
        self.settings.airplay_volume = float(value)
        self.settings.clamped().save()
        self.airplay_volume_set_on = None   # re-applied on the next tick
        await self.tick()

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
        if default_sink_name(objects) != WHOLE_HOME:
            await _run("wpctl", "set-default", str(self.whole_home_id))
            log.info("default output -> %s", WHOLE_HOME)
        if self.airplay_id is not None and self.airplay_volume_set_on != self.airplay_id:
            await _run("wpctl", "set-volume", str(self.airplay_id), f"{self.settings.airplay_volume:.2f}")
            self.airplay_volume_set_on = self.airplay_id
        want = self.policy.want_feed(now) and self.airplay_id is not None
        feed_up = self.feed is not None and self.feed.returncode is None
        if want and not feed_up:
            self.feed = await asyncio.create_subprocess_exec(
                *feed_cmd(), stdout=asyncio.subprocess.DEVNULL, env=pw._env())
            log.info("AirPlay feed on -> %s", self.settings.airplay)
        elif not want and feed_up:
            await self._stop_feed()
            log.info("AirPlay feed off (nothing playing)")

    async def _start(self, dac_name: str) -> None:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        conf = CONF_DIR / "outputs.conf"
        conf.write_text(outputs_conf(self.settings, dac_name))
        self.proc = await asyncio.create_subprocess_exec(
            "pipewire", "-c", str(conf), stdout=asyncio.subprocess.DEVNULL, env=pw._env())
        self.airplay_volume_set_on = None
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
        self.whole_home_id = self.airplay_id = None

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
