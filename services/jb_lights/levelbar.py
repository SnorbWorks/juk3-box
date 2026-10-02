"""Level bar controller: runs a default theme, and temporarily shows the
volume fill (theme4) while the volume is being adjusted.

Listens to jukebox/jb-audio/state. jb-audio publishes it on every volume or
mute command (and once at its own startup), so a message whose volume/mute
differs from the last known state means the knob is being turned: crossfade
to the volume fill, keep it up while changes keep coming, and crossfade back
to the default theme OVERLAY_HOLD_S after the last change. The starting
volume is read from PipeWire directly, since jb-audio's state isn't retained
and startup must not trigger the overlay.

Themes 1-3 still run on fake audio until real levels from PipeWire exist.

    python -m services.jb_lights.levelbar --theme 3
"""

import argparse
import json
import os
import subprocess
import threading
import time

import paho.mqtt.client as mqtt

from services.jb_lights import matrix
from services.jb_lights.themes import (
    volume_bar_theme1 as theme1,
    volume_bar_theme2 as theme2,
    volume_bar_theme3 as theme3,
)
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


THEMES = {1: Theme1, 2: Theme2, 3: Theme3}


def read_pipewire_volume():
    out = subprocess.run(
        ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
        env={**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")},
    ).stdout
    parts = out.split()
    return float(parts[1]), "[MUTED]" in out


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", type=int, choices=sorted(THEMES), default=3)
    ap.add_argument("--host", default="localhost")
    args = ap.parse_args()

    watcher = VolumeWatcher(*read_pipewire_volume())
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p=None: c.subscribe("jukebox/jb-audio/state")
    client.on_message = lambda c, u, msg: watcher.on_state(json.loads(msg.payload))
    client.connect(args.host, 1883)
    client.loop_start()

    theme = THEMES[args.theme]()
    volume_fill = VolumeFill(*watcher.snapshot()[:2])
    # Themes and the fill scale their own brightness; the driver only enforces the cap.
    bar = WS2812SPI(matrix.NUM_PIXELS, brightness=1.0, max_amps=matrix.MAX_AMPS)
    alpha = 0.0  # 0 = default theme, 1 = volume fill
    start = last = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            dt, t = now - last, now - start
            last = now
            volume, muted, last_change = watcher.snapshot()
            want = 1.0 if last_change and now - last_change < OVERLAY_HOLD_S else 0.0
            step = dt / FADE_S
            alpha = min(want, alpha + step) if want > alpha else max(want, alpha - step)

            base = theme.frame(t, dt)
            fill = volume_fill.frame(volume, muted)
            bar.show(base if alpha == 0 else fill if alpha == 1 else _mix(base, fill, alpha))
            time.sleep(1 / FPS)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        bar.off()
        bar.close()


if __name__ == "__main__":
    main()
