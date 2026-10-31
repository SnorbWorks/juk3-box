"""buttons-music: the keypad joins the light show while music plays.

Follows the same look as the rest of the cabinet (the shared AudioFeed):
  eq      letters and digits are two little graphic EQs, bass on the left:
          each button's colour runs red (bass) .. violet (treble) like the
          panel's columns, its brightness its band's level
  ribbon  the ribbon's palette (white-hot core, pink, purple, deep blue)
          rolls across the letters and digits, swelling with the music and
          flaring hot on the beat
  show    the keypad's part of the light show (show/parts.py Keys): the
          sparkle -- hats, accents -- in the song's palette
  idle    white at 70% -- buttons-theme1's resting look
SINGLE/ALBUM sit behind coloured filters (blue is blocked), so they pulse
white on the beat; RESET does the same. A drop flashes the keypad white.

Selection feedback always wins: buttons-theme1 only hands the keypad to this
when nothing is waiting (no letter, Reset not held, no power menu), and the
show fades back in after each interaction.

Pure: frame() returns (r, g, b, w) per pixel; the caller writes them.
"""

import colorsys
import math

from services.jb_lights import buttons
from services.jb_lights.show.parts import Keys
from services.jb_lights.themes.volume_bar_theme2 import HOT, _lerp, _palette

IDLE_W = int(255 * 0.70)              # buttons-theme1's idle white
FADE_S = 1.5
DROP_FLASH_S = 0.8
LEVEL = 0.85                          # colour brightness at full band level
FLOOR = 0.08                          # an empty band still glows a little
PULSE_W = (40, 150)                   # white buttons: resting .. on the beat

# Left-to-right position (0..1) of each letter and digit as they sit on the keypad.
ROW_X = {}
for row in (buttons.LETTERS, buttons.DIGITS):
    for k, key in enumerate(row):
        ROW_X[buttons.index(key)] = k / (len(row) - 1)
WHITE_KEYS = (buttons.SINGLE, buttons.ALBUM, buttons.RESET)


def _rgbw(rgb, k=1.0):
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k), 0)


def _mix(a, b, k):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


class ButtonsMusic:
    def __init__(self):
        self.current, self.previous, self.fade = "idle", None, 1.0
        self.clock = 0.0
        self.keys = Keys()
        self.idle_keys = Keys()

    def resume(self):
        """Back from an interaction (the keypad showed plain white): fade
        from white into the show rather than snapping."""
        if self.current != "idle":
            self.previous, self.fade = "idle", 0.0

    def _render(self, look, dt, snap):
        out = {}
        beat = snap.beat()
        if look in ("show", "idle-show"):
            return (self.keys if look == "show" else self.idle_keys).frame(snap.cues, snap, dt)
        if look == "eq":
            bands = snap.resample(10)
            gain = 0.5 + 0.5 * snap.level
            for i, x in ROW_X.items():
                v = min(1.0, bands[round(x * 9)] * gain)
                r, g, b = colorsys.hsv_to_rgb(0.75 * x, 1.0, FLOOR + (LEVEL - FLOOR) * v)
                out[i] = (int(r * 255), int(g * 255), int(b * 255), 0)
        elif look == "ribbon":
            self.clock += dt * (0.5 + 1.0 * snap.level)
            swell = 0.7 + 0.6 * snap.level
            for i, x in ROW_X.items():
                d = abs(math.sin(math.pi * (0.9 * x - 0.35 * self.clock))) / swell
                colour = _palette(min(d, 1.0)) if d < 1.15 else (0, 0, 0)
                if beat > 0 and d < 0.6:
                    colour = _lerp(colour, HOT, 0.6 * beat)
                out[i] = _rgbw(colour, LEVEL * min(1.0, (1.15 - d) / 0.4) if d < 1.15 else 0)
        else:
            for i in range(buttons.NUM_PIXELS):
                out[i] = (0, 0, 0, IDLE_W)
            return out
        w = int(PULSE_W[0] + (PULSE_W[1] - PULSE_W[0]) * beat)
        for i in WHITE_KEYS:
            out[i] = (0, 0, 0, w)
        return out

    def frame(self, dt, snap):
        """Colours per pixel, or None when resting idle (nothing to animate)."""
        if snap.look != self.current:
            self.previous, self.current, self.fade = self.current, snap.look, 0.0
        if self.current == "idle" and self.fade >= 1.0:
            return None
        self.fade = min(1.0, self.fade + dt / FADE_S)
        out = self._render(self.current, dt, snap)
        if self.fade < 1.0 and self.previous:
            before = self._render(self.previous, 0.0, snap)
            out = {i: _mix(before[i], out[i], self.fade) for i in out}
        if snap.look in ("eq", "ribbon") and snap.drop_age < DROP_FLASH_S:
            k = 0.7 * (1 - snap.drop_age / DROP_FLASH_S) ** 2
            out = {i: _mix(c, (c[0], c[1], c[2], 255), k) for i, c in out.items()}
        return out
