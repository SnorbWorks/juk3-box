"""The owner's display settings and what they mean for sway, kept free of
I/O so they can be tested.

- Picture area: how far each side of the screen's contents is pulled in
  from the panel's edge, in pixels, so they fit the window in the cabinet's
  glass. The kiosk page does the fitting (jb_ui engine display.js): the
  background still fills the panel. Sides are as you see them (top is the top of the picture), and
  they're kept on the same part of the glass when the rotation changes.
- Rotation: for a panel mounted sideways or upside down (clockwise).
- Screen sleep: the panel turns off after this many idle minutes with
  nothing playing. 0 = never.
"""

SIDES = ("top", "right", "bottom", "left")          # clockwise
ROTATIONS = (0, 90, 180, 270)
SLEEP_CHOICES = (0, 5, 10, 15, 30, 60, 120)
# No side may take more than this share of the screen's width or height,
# so at least half the picture is always left to see (and to fix it with).
MAX_SHARE = 0.25
# Used for limits until sway has told us the real size.
FALLBACK_SIZE = (1920, 1080)

DEFAULTS = {"inset": {s: 0 for s in SIDES}, "rotation": 0, "sleep_minutes": 0}


def _int(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def clean(raw) -> dict:
    """Saved settings with anything unusable replaced by the default."""
    raw = raw if isinstance(raw, dict) else {}
    inset_raw = raw.get("inset") if isinstance(raw.get("inset"), dict) else {}
    inset = {s: max(0, _int(inset_raw.get(s)) or 0) for s in SIDES}
    rotation = raw.get("rotation") if raw.get("rotation") in ROTATIONS else 0
    sleep = raw.get("sleep_minutes") if raw.get("sleep_minutes") in SLEEP_CHOICES else 0
    return {"inset": inset, "rotation": rotation, "sleep_minutes": sleep}


def logical_size(mode_size: tuple[int, int], rotation: int) -> tuple[int, int]:
    """The screen's width and height as the picture sees it: a panel turned
    on its side is taller than it is wide."""
    w, h = mode_size
    return (h, w) if rotation in (90, 270) else (w, h)


def limits(size: tuple[int, int] | None) -> dict:
    """The most each side can be pulled in, for a screen this size."""
    w, h = size or FALLBACK_SIZE
    return {"top": int(h * MAX_SHARE), "bottom": int(h * MAX_SHARE),
            "left": int(w * MAX_SHARE), "right": int(w * MAX_SHARE)}


def clamp(inset: dict, size: tuple[int, int] | None) -> dict:
    most = limits(size)
    return {s: max(0, min(most[s], _int(inset.get(s)) or 0)) for s in SIDES}


def remap(inset: dict, old_rotation: int, new_rotation: int) -> dict:
    """The same physical sides of the panel, as seen after a rotation.

    Turning the picture 90 degrees clockwise puts its top where its right
    side was, so the new top takes what the old right side had."""
    k = ((new_rotation - old_rotation) // 90) % 4
    return {SIDES[i]: inset[SIDES[(i + k) % 4]] for i in range(4)}


def transform_name(rotation: int) -> str:
    return "normal" if rotation == 0 else str(rotation)


def sway_layout(settings: dict) -> str:
    """The swaymsg command that turns the screen as the owner wants."""
    return f"output * transform {transform_name(settings['rotation'])}"


class Sleep:
    """When the screen should be off: after `minutes` without anyone at the
    controls and with nothing playing. Anything at the controls, or music
    starting, wakes it."""

    def __init__(self, now: float):
        self.last_activity = now
        self.playing: dict[str, bool] = {}
        self.asleep = False

    def activity(self, now: float) -> bool:
        """Someone used the knob or keypad. True if that woke the screen."""
        self.last_activity = now
        return self._wake()

    def source(self, name: str, playing: bool, now: float) -> bool:
        """A source reported what it's doing. True if that woke the screen."""
        was = any(self.playing.values())
        self.playing[name] = playing
        if playing:
            return self._wake()
        if was and not any(self.playing.values()):
            self.last_activity = now     # the idle time counts from the music stopping
        return False

    def wake(self, now: float) -> bool:
        self.last_activity = now
        return self._wake()

    def _wake(self) -> bool:
        woke = self.asleep
        self.asleep = False
        return woke

    def due(self, now: float, minutes: int) -> bool:
        """True when the screen should go to sleep now."""
        if self.asleep or not minutes or any(self.playing.values()):
            return False
        if now - self.last_activity < minutes * 60:
            return False
        self.asleep = True
        return True
