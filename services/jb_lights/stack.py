"""One chain's finishing steps, the same in every loop (accent_lights,
levelbar, buttons-theme1): the modules' layers (show/layers.py), then the
safety pass (safety.py). The look itself comes before; the driver's current
cap after.

    stack = Stack("accent", accent.positions(), inputs, feed.director)
    frame = stack(frame, now, dt, snap)        # or .modules(), feedback, then .safe()
"""

import time

from services.jb_lights.safety import Safety
from services.jb_lights.show.layers import ChainLayers


class Stack:
    def __init__(self, chain: str, positions, inputs=None, director=None, rgbw=False, floor=0.0):
        self.layers = ChainLayers(chain, positions, rgbw=rgbw)
        self.safety = Safety(floor)
        self.inputs, self.director = inputs, director

    def settings(self, now: float) -> dict:
        lib = self.director.library
        lib.refresh(now=now)                        # every REFRESH_S at most; idle never picks a show
        return lib.settings

    def modules(self, frame, now: float, snap=None):
        """The modules' layers on the look (signals, cues, overlays)."""
        if self.director is None or self.inputs is None:
            return frame
        show = self.director.style if snap is not None and snap.look == "show" else None
        return self.layers.apply(frame, now, self.inputs.state(now), self.settings(now), show)

    def safe(self, frame, now: float, dt: float):
        """The safety pass: last, after any feedback."""
        if self.director is None:
            return frame
        return self.safety.apply(frame, now, dt, self.settings(now))

    def __call__(self, frame, now: float | None = None, dt: float = 0.025, snap=None):
        now = time.monotonic() if now is None else now
        return self.safe(self.modules(frame, now, snap), now, dt)
