"""volume_bar-theme4: volume fill.

A visual volume indicator, shown as an overlay while the volume knob is being
turned (see services.jb_lights.levelbar): white columns fill left -> right in
proportion to the volume (50% = 18 of 36 columns), the edge column fading in
proportionally so small steps are visible, gliding smoothly to each new
level. Muted: the fill drops to a dim glow so the level stays visible.

The white is scaled so a completely full bar (100% volume) stays under the
4A cap -- otherwise the limiter would dim every column as the volume rose.
"""

from services.jb_lights import matrix

BRIGHTNESS = 0.35      # 180 px x 3 ch x 20 mA x 0.35 = ~3.8 A when full
GLIDE = 0.25           # fraction of the remaining gap closed per frame
MUTED_LEVEL = 0.15     # fill brightness while muted
WHITE = (255, 255, 255)


class VolumeFill:
    def __init__(self, volume: float = 0.0, muted: bool = False):
        self.shown = volume * matrix.COLS  # displayed fill, in columns (fractional)
        self.level = MUTED_LEVEL if muted else 1.0

    def frame(self, volume: float, muted: bool):
        """volume: 0..1. Returns a full frame, already brightness-scaled."""
        target_cols = max(0.0, min(1.0, volume)) * matrix.COLS
        target_level = MUTED_LEVEL if muted else 1.0
        self.shown += (target_cols - self.shown) * GLIDE
        self.level += (target_level - self.level) * GLIDE
        frame = matrix.blank()
        for c in range(matrix.COLS):
            k = max(0.0, min(1.0, self.shown - c)) * self.level * BRIGHTNESS  # edge column fades in
            px = tuple(int(v * k) for v in WHITE)
            for row in range(matrix.ROWS):
                frame[matrix.index(c, row)] = px
        return frame
