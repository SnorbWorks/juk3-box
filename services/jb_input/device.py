"""Stage 1: find, grab and read the Pico keypad through evdev.

Prefers the stable udev symlink (/dev/input/jb-keypad, from
deploy/udev/99-jb-keypad.rules) and falls back to scanning for the device by
name. The device is grabbed exclusively, so key presses never reach the
console or the kiosk browser. Unplugging surfaces as DeviceLost; the runner
reconnects.
"""

import os

from services.jb_input.events import RawEvent


class DeviceLost(Exception):
    pass


class EvdevKeypad:
    def __init__(self, dev, grab: bool):
        self.dev = dev
        self.grabbed = False
        if grab:
            dev.grab()
            self.grabbed = True

    @property
    def name(self) -> str:
        return self.dev.name

    @property
    def path(self) -> str:
        return self.dev.path

    def active_key_names(self) -> list:
        from evdev import ecodes
        return [_code_name(ecodes.KEY, c) for c in self.dev.active_keys()]

    async def events(self, clock):
        """Yields RawEvent for every key event; raises DeviceLost on unplug."""
        from evdev import ecodes
        try:
            async for ev in self.dev.async_read_loop():
                if ev.type == ecodes.EV_KEY:
                    yield RawEvent(_code_name(ecodes.KEY, ev.code), ev.value, clock())
        except OSError as e:  # ENODEV once the Pico is unplugged
            raise DeviceLost(str(e)) from e

    def close(self):
        try:
            if self.grabbed:
                self.dev.ungrab()
        except OSError:
            pass
        try:
            self.dev.close()
        except OSError:
            pass


def _code_name(table, code: int) -> str:
    name = table.get(code, f"KEY_{code}")
    return name[0] if isinstance(name, (list, tuple)) else name  # some codes have aliases


def open_keypad(settings) -> EvdevKeypad | None:
    """The keypad, grabbed, or None if it isn't plugged in."""
    import evdev
    candidates = []
    if settings.device_path and os.path.exists(settings.device_path):
        candidates.append(settings.device_path)
    candidates += evdev.list_devices()
    for path in candidates:
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if path == settings.device_path or dev.name == settings.device_name:
            return EvdevKeypad(dev, settings.grab)
        dev.close()
    return None
