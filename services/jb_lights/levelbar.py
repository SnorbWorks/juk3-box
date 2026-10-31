"""Level bar controller: runs a default theme, and temporarily shows the
volume fill (theme4) while the volume is being adjusted.

Listens to jukebox/jb-audio/state. jb-audio publishes it on every volume or
mute command (and once at its own startup), so a message whose volume/mute
differs from the last known state means the knob is being turned: crossfade
to the volume fill, keep it up while changes keep coming, and crossfade back
to the default theme OVERLAY_HOLD_S after the last change. The starting
volume is read from PipeWire directly, since jb-audio's state isn't retained
and startup must not trigger the overlay.

Theme 0 (idle, all white) and 5 (colour stripes) are idle looks. In the service the music picks the
look (MusicLooks, from jb-analyzer via the shared AudioFeed); run standalone,
themes 1-3 play on fake audio.

    python -m services.jb_lights.levelbar --theme 0

run() is what the jb-lights service starts, in its own thread.
"""

import argparse
import json
import os
import subprocess
import threading
import time

import paho.mqtt.client as mqtt

from services.jb_lights import matrix
from services.jb_lights.show.parts import BarShow
from services.jb_lights.stack import Stack
from services.jb_lights.themes import (
    volume_bar_theme1 as theme1,
    volume_bar_theme2 as theme2,
    volume_bar_theme3 as theme3,
)
from services.jb_lights.themes.volume_bar_idle import Idle
from services.jb_lights.themes.volume_bar_stripes import Stripes
from services.jb_lights.themes.volume_bar_theme4 import VolumeFill
from services.jb_lights.ws2812_spi import WS2812SPI

FPS = 50
OVERLAY_HOLD_S = 3.0   # how long the volume fill stays after the last change
FADE_S = 0.25          # crossfade time in each direction


def _scale(frame, k):
    return [tuple(int(v * k) for v in px) for px in frame]


def _mix(a, b, alpha):
    return [tuple(int(x + (y - x) * alpha) for x, y in zip(pa, pb)) for pa, pb in zip(a, b)]


class Theme1:
    def __init__(self):
        self.levels = [0.0] * matrix.COLS

    def frame(self, t, dt):
        self.levels = theme1.smooth(self.levels, theme1.fake_spectrum(t))
        return _scale(theme1.render(self.levels), theme1.BRIGHTNESS)


class Theme2:
    def __init__(self):
        self.ribbon = theme2.Ribbon()

    def frame(self, t, dt):
        return _scale(self.ribbon.frame(t), theme2.BRIGHTNESS)


class Theme3:
    def __init__(self):
        self.analyzer = theme3.Analyzer()

    def frame(self, t, dt):
        return _scale(self.analyzer.frame(theme3.fake_spectrum(t), dt), theme3.BRIGHTNESS)


THEMES = {0: Idle, 1: Theme1, 2: Theme2, 3: Theme3, 5: Stripes}   # 4 is the volume fill

DROP_FLASH_S = 0.8
DROP_FLASH = 0.6
LOOK_FADE_S = 1.5


class MusicLooks:
    """The bar following the music (the shared AudioFeed's look):
    idle -> the default theme, show -> its part of the light show (show/parts.py),
    eq -> theme 3 (the classic analyzer) on the
    real bands, ribbon -> theme 2 swelling with the music and flaring on the
    beat. Looks crossfade; a drop flashes white. Pure (no LEDs)."""

    def __init__(self, idle_theme):
        self.idle = idle_theme
        self.analyzer = theme3.Analyzer()
        self.ribbon = theme2.Ribbon()
        self.ribbon_clock = 0.0
        self.current, self.previous, self.fade = "idle", None, 1.0
        self.show = BarShow()

    def _render(self, look, t, dt, snap):
        if look == "show":
            return self.show.frame(t, dt, snap)
        if look == "eq":
            gain = 0.5 + 0.5 * snap.level
            cols = [min(1.0, v * gain) for v in snap.resample(matrix.COLS)]
            return _scale(self.analyzer.frame(cols, dt), theme3.BRIGHTNESS)
        if look == "ribbon":
            self.ribbon_clock += dt * (0.5 + 1.0 * snap.level)
            frame = self.ribbon.frame(self.ribbon_clock, swell=0.7 + 0.6 * snap.level, ignite=snap.beat_age <= dt)
            return _scale(frame, theme2.BRIGHTNESS)
        return self.idle.frame(t, dt)

    def frame(self, t, dt, snap):
        if snap.look != self.current:
            self.previous, self.current, self.fade = self.current, snap.look, 0.0
        self.fade = min(1.0, self.fade + dt / LOOK_FADE_S)
        out = self._render(self.current, t, dt, snap)
        if self.fade < 1.0 and self.previous:
            out = _mix(self._render(self.previous, t, dt, snap), out, self.fade)
        if snap.look in ("eq", "ribbon") and snap.drop_age < DROP_FLASH_S:
            k = DROP_FLASH * (1 - snap.drop_age / DROP_FLASH_S) ** 2
            out = _mix(out, [(255, 255, 255)] * len(out), k)
        return out


def read_pipewire_volume():
    """(volume, muted) from PipeWire, or (0.0, False) if it can't be read
    (e.g. PipeWire not up yet at boot); jb-audio's next state fixes it."""
    try:
        out = subprocess.run(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
            env={**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")},
            timeout=5,
        ).stdout
        return float(out.split()[1]), "[MUTED]" in out
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return 0.0, False


class VolumeWatcher:
    """Tracks jb-audio's volume and when it last changed."""

    def __init__(self, volume, muted):
        self.volume, self.muted = volume, muted
        self.last_change = 0.0  # monotonic time of the last real change
        self._lock = threading.Lock()

    def on_state(self, data):
        with self._lock:
            volume = data.get("volume")
            if not isinstance(volume, (int, float)):
                return
            volume, muted = max(0.0, min(1.0, float(volume))), bool(data.get("muted"))
            if abs(volume - self.volume) > 1e-4 or muted != self.muted:
                self.last_change = time.monotonic()
            self.volume, self.muted = volume, muted

    def snapshot(self):
        with self._lock:
            return self.volume, self.muted, self.last_change


def run(theme_num=0, host="localhost", stop=None, feed=None, inputs=None):
    """Drives the bar until stop (a threading.Event) is set, or Ctrl-C. With
    a feed (AudioFeed), theme_num is the idle look and the music picks the rest."""
    stop = stop or threading.Event()
    watcher = VolumeWatcher(*read_pipewire_volume())
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: c.subscribe("jukebox/jb-audio/state")
    client.on_message = lambda c, u, msg: watcher.on_state(json.loads(msg.payload))
    client.connect(host, 1883)
    client.loop_start()

    theme = THEMES[theme_num]()
    music = MusicLooks(theme) if feed else None
    volume_fill = VolumeFill(*watcher.snapshot()[:2])
    # Themes and the fill scale their own brightness; the driver only enforces the cap.
    bar = WS2812SPI(matrix.NUM_PIXELS, brightness=1.0, max_amps=matrix.MAX_AMPS)
    stack = Stack("levelbar", matrix.positions(), inputs, feed.director if feed else None)
    alpha = 0.0  # 0 = default theme, 1 = volume fill
    start = last = time.monotonic()
    try:
        while not stop.is_set():
            now = time.monotonic()
            dt, t = now - last, now - start
            last = now
            volume, muted, last_change = watcher.snapshot()
            want = 1.0 if last_change and now - last_change < OVERLAY_HOLD_S else 0.0
            step = dt / FADE_S
            alpha = min(want, alpha + step) if want > alpha else max(want, alpha - step)

            snap = feed.snapshot(now) if music else None
            base = music.frame(t, dt, snap) if music else theme.frame(t, dt)
            base = stack.modules(base, now, snap)     # the volume fill is feedback: over the modules
            fill = volume_fill.frame(volume, muted)
            bar.show(stack.safe(base if alpha == 0 else fill if alpha == 1 else _mix(base, fill, alpha), now, dt))
            time.sleep(1 / FPS)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        bar.off()
        bar.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", type=int, choices=sorted(THEMES), default=0)
    ap.add_argument("--host", default="localhost")
    args = ap.parse_args()
    run(args.theme, args.host)


if __name__ == "__main__":
    main()
