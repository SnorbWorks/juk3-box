"""The safety pass: the last step before any chain's pixels go out
(docs/contracts/lights.md, Layers and safety). Nothing gets past it:

  brightness    the owner's cap, or the quiet-hours one while that's on
                (whichever is lower); the keypad never goes below FLOOR_KEYS
                so it can always be read
  low-flash     the owner's setting: no pixel brightens faster than over
                LOW_FLASH_RISE_S, so a white flash becomes a soft swell
  flash limit   a jump of the whole chain to bright counts as a flash; past
                MAX_FLASHES in a second, further jumps are held back to a
                slow rise
  current       each chain's max_amps, still applied by its driver
                (accent_diner.limit, WS2812SPI) after this

Pure: Safety.apply() takes a frame and returns it made safe.
"""

import time

from services.jb_lights import owner

MAX_FLASHES = 3             # a second
FLASH_JUMP = 0.25           # the chain's mean brightness rising this much in one frame (0..1) is a flash
HELD_RISE = 0.06            # past the limit, the mean may rise only this much a frame
LOW_FLASH_RISE_S = 0.5      # low-flash: off to full no faster than this
FLOOR_KEYS = 0.3


def local_minutes() -> int:
    t = time.localtime()
    return t.tm_hour * 60 + t.tm_min


def _mean(frame) -> float:
    if not frame:
        return 0.0
    return sum(sum(px) for px in frame) / (255 * len(frame) * len(frame[0]))


class Safety:
    def __init__(self, floor: float = 0.0):
        self.floor = floor
        self.prev = None
        self.flashes: list[float] = []

    def brightness(self, settings: dict, minutes: int | None = None) -> float:
        b = settings["brightness"]
        if owner.quiet_now(settings, local_minutes() if minutes is None else minutes):
            b = min(b, settings["quiet"]["brightness"])
        return max(self.floor, b)

    def apply(self, frame, now: float, dt: float, settings: dict, minutes: int | None = None) -> list:
        b = self.brightness(settings, minutes)
        if b < 1:
            frame = [tuple(int(v * b) for v in px) for px in frame]
        prev = self.prev if self.prev is not None and len(self.prev) == len(frame) else None
        if prev is not None:
            if settings["low_flash"]:
                step = max(1, int(255 * dt / LOW_FLASH_RISE_S))
                frame = [tuple(min(v, p + step) for v, p in zip(px, pp)) for px, pp in zip(frame, prev)]
            else:
                jump = _mean(frame) - _mean(prev)
                if jump > FLASH_JUMP:
                    self.flashes = [t for t in self.flashes if now - t < 1.0]
                    if len(self.flashes) >= MAX_FLASHES:
                        k = HELD_RISE / jump
                        frame = [tuple(int(p + (v - p) * k) if v > p else v for v, p in zip(px, pp))
                                 for px, pp in zip(frame, prev)]
                    else:
                        self.flashes.append(now)
        self.prev = frame
        return frame
