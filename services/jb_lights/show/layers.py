"""The engine's layers: what modules' lights do on top of the look.

Every frame, each chain stacks (docs/contracts/lights.md, Layers):
  base      the look: the idle look, or the music show
  signals   modules' palettes tint it, their intensity scales it
  cues      named one-offs (lightning, a doorbell) as short effects
  overlays  a module taking part of the cabinet for a while
  feedback  the keypad's own (a pick, Reset, the power menu): always wins,
            drawn by buttons-theme1 outside these layers
then the safety pass (safety.py).

Pure: ChainLayers.apply() takes a frame and the inputs' state and returns
the frame to show. With no module input it hands the frame straight back.
"""

import math

from services.jb_lights import fixtures, owner

# What a cue can turn into (a show's inputs.cues maps cue names to these).
CUE_EFFECTS = {
    "flash",      # the whole cabinet white, fading (a flash: counted against the flash limit)
    "flicker",    # lightning: two or three quick white flickers
    "pulse",      # one swell of the cue's colour
    "sparkle",    # a shower of sparkles in the cue's colour
    "sweep",      # a band of the cue's colour crossing the cabinet
    "ignore",     # this show doesn't react to it
}
DEFAULT_CUE = "pulse"       # a cue the show doesn't name
DEFAULT_TINT = 0.35         # how far a palette signal tints a show that doesn't say (and the idle look)
CUE_S = {"flash": 0.6, "flicker": 0.6, "pulse": 1.2, "sparkle": 1.5, "sweep": 1.2}
FLICKERS = ((0.0, 0.06), (0.15, 0.22), (0.34, 0.40))
SWEEP_W = 0.15
SPARKLE_SHARE = 0.18        # of the pixels, in a sparkle cue
WHITE = (255, 255, 255)


def _hash(i: int, salt: float) -> float:
    """A steady 0..1 per pixel (and per cue), so sparkles don't jump about."""
    h = (i * 2654435761 + int(salt * 1000) * 40503) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 2246822519) & 0xFFFFFFFF
    return (h ^ (h >> 13)) / 0xFFFFFFFF


def _mix(px, c, k):
    """px toward colour c (rgb) by k; px is (r, g, b) or (r, g, b, w)."""
    if k <= 0:
        return px
    k = min(1.0, k)
    if len(px) == 4:
        if c[0] == c[1] == c[2]:                    # white goes to the white LED
            c = (0, 0, 0, c[0])
        else:
            c = (*c, 0)
    return tuple(int(a + (b - a) * k) for a, b in zip(px, c))


def _scaled(c, k):
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


class ChainLayers:
    """The layers for one chain. positions: (x, y) per pixel, or x only
    (the keypad's ROW_X); rgbw: the chain's pixels are (r, g, b, w)."""

    def __init__(self, chain: str, positions, cabinet=None, rgbw=False):
        cabinet = cabinet or fixtures.CABINET
        self.chain, self.rgbw = chain, rgbw
        self.n = cabinet.pixels(chain)
        self.x = [(p[0] if isinstance(p, tuple) else p) if p is not None else 0.5 for p in positions]
        self.on: dict[str, list[int]] = {}         # role or fixture id -> its pixels on this chain
        for f in cabinet.chains[chain]["fixtures"]:
            px = list(range(f["start"], f["start"] + f["count"]))
            self.on.setdefault(f["role"], []).extend(px)
            self.on[f["id"]] = px

    def apply(self, frame, now: float, state: dict | None, settings: dict, show=None) -> list:
        if not state or not (state["signals"] or state["cues"] or state["overlays"]):
            return frame
        frame = list(frame)
        ins = getattr(show, "inputs", None) or {}
        frame = self._signals(frame, state["signals"], settings, ins.get("palette", DEFAULT_TINT))
        frame = self._cues(frame, now, state, settings, ins.get("cues", {}))
        return self._overlays(frame, now, state["overlays"], settings)

    def _signals(self, frame, signals, settings, tint):
        if not signals:
            return frame
        dim, palette, k = 1.0, None, 0.0
        for mod, s in sorted(signals.items(), key=lambda kv: kv[1]["at"]):
            g = owner.input_gain(settings, mod)
            if "intensity" in s:
                dim *= 1 - (1 - s["intensity"]) * g
            if "palette" in s and g > 0:
                palette, k = s["palette"], tint * g          # the latest palette wins
        if palette and k > 0:
            frame = self._tint(frame, palette, min(1.0, k))
        if dim < 1:
            frame = [tuple(int(v * dim) for v in px) for px in frame]
        return frame

    def _tint(self, frame, palette, k):
        """Each pixel toward its palette colour (by x) at its own brightness.
        A signal is usually up all the time (the weather), so this is kept
        cheap: each pixel's colour, pre-scaled by k / 255, is worked out once
        per palette."""
        key = (tuple(palette), k)
        if getattr(self, "_tint_key", None) != key:
            n, f = len(palette), k / 255
            cols = [palette[min(n - 1, int(x * n))] for x in self.x]
            self._tint_key = key
            self._tint_cols = [(r * f, g * f, b * f) for r, g, b in cols]
        j = 1 - k
        if self.rgbw:
            return [(int(p[0] * j + c[0] * m), int(p[1] * j + c[1] * m), int(p[2] * j + c[2] * m), int(p[3] * j))
                    for p, c in zip(frame, self._tint_cols) for m in (max(p),)]
        return [(int(p[0] * j + c[0] * m), int(p[1] * j + c[1] * m), int(p[2] * j + c[2] * m))
                for p, c in zip(frame, self._tint_cols) for m in (max(p),)]

    def _cues(self, frame, now, state, settings, cue_map):
        for cue in state["cues"]:
            effect = cue_map.get(cue["cue"], DEFAULT_CUE)
            if effect == "ignore":
                continue
            age = now - cue["at"]
            if not 0 <= age < CUE_S[effect]:
                continue
            g = owner.input_gain(settings, cue["module"]) * cue["strength"]
            if g <= 0:
                continue
            sig = state["signals"].get(cue["module"], {})
            colour = cue["color"] or (sig.get("palette") or [WHITE])[0]
            frame = self._cue(frame, effect, age, g, colour, cue["at"])
        return frame

    def _cue(self, frame, effect, age, g, colour, salt):
        d = CUE_S[effect]
        if effect == "flash":
            k = g * (1 - age / d) ** 2
            return [_mix(px, WHITE, k) for px in frame]
        if effect == "flicker":
            on = any(a <= age < b for a, b in FLICKERS)
            return [_mix(px, WHITE, g) for px in frame] if on else frame
        if effect == "pulse":
            k = 0.8 * g * math.sin(math.pi * age / d)
            return [_mix(px, colour, k) for px in frame]
        if effect == "sparkle":
            fade = g * (1 - age / d)
            out = []
            for i, px in enumerate(frame):
                h = _hash(i, salt)
                out.append(_mix(px, colour, fade * (1 - 0.6 * h / SPARKLE_SHARE)) if h < SPARKLE_SHARE else px)
            return out
        # sweep: a band crossing left to right
        c = -SWEEP_W + (1 + 2 * SWEEP_W) * age / d
        return [_mix(px, colour, g * (1 - abs(self.x[i] - c) / SWEEP_W)) if abs(self.x[i] - c) < SWEEP_W else px
                for i, px in enumerate(frame)]

    def _overlays(self, frame, now, overlays, settings):
        for o in overlays:                          # lowest priority first, so the highest ends on top
            px = self.on.get(o["on"])
            if not px:
                continue
            g = owner.input_gain(settings, o["module"])
            if g <= 0:
                continue
            look, colour = o["look"], o["look"]["color"]
            t = now - o["at"]
            if look["kind"] == "fill":
                for i in px:
                    frame[i] = _mix(frame[i], _scaled(colour, look["level"]), g)
            elif look["kind"] == "bar":
                xs = [self.x[i] for i in px]
                lo, hi = min(xs), max(xs)
                for i in px:
                    lit = (self.x[i] - lo) / ((hi - lo) or 1) < look["level"] - 1e-9 or look["level"] >= 1
                    frame[i] = _mix(frame[i], colour if lit else (0, 0, 0), g)
            elif look["kind"] == "pulse":
                k = 0.15 + 0.85 * (0.5 - 0.5 * math.cos(2 * math.pi * look["speed"] * t))
                for i in px:
                    frame[i] = _mix(frame[i], _scaled(colour, k * look["level"]), g)
            else:                                   # sparkle: twinkling over a dark part
                for i in px:
                    k = max(0.0, math.sin(2 * math.pi * (look["speed"] * t + _hash(i, 7.0)))) ** 4
                    frame[i] = _mix(frame[i], _scaled(colour, k * look["level"]), g)
        return frame
