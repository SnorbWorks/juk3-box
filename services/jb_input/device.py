"""Stage 1: find, grab and read the keypads and keyboards through evdev.

Every input device with keys gets a role:
  keypad    the cabinet's buttons: our Pico (the stable udev symlink
            /dev/input/jb-keypad from deploy/udev/99-jb-keypad.rules, or its
            name), or a device the setup wizard learned (its id in
            settings.keypads)
  keyboard  any other keyboard (it has letters, numbers and Enter); it
            always works, with the keyboard keymap
  other     anything else with keys (an arcade button board that calls
            itself a gamepad, say): opened only while the setup wizard is
            learning, so it can be taught, and never grabbed
Keypads and keyboards are grabbed exclusively, so key presses never reach the
console or the kiosk browser. Unplugging surfaces as DeviceLost; the runner
opens it again when it comes back.
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

    @property
    def id(self) -> str:
        return device_id(self.dev)

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


def device_id(dev) -> str:
    """Which device this is, the same after a replug or reboot (unlike its
    /dev/input path): USB vendor, product and name."""
    return f"{dev.info.vendor:04x}:{dev.info.product:04x}:{dev.name}"


# The Pi's own: the HDMI ports (a TV's remote over CEC) and the power button.
NOT_CONTROLS = ("vc4-hdmi", "pwr_button")


def role_of(dev, settings) -> str | None:
    from evdev import ecodes
    keypad_path = os.path.realpath(settings.device_path) if settings.device_path else None
    if (dev.path == keypad_path or dev.name.startswith(settings.device_name)
            or device_id(dev) in settings.keypads):
        return "keypad"
    keys = set(dev.capabilities().get(ecodes.EV_KEY, []))
    if not keys or dev.name.startswith(NOT_CONTROLS):
        return None
    if settings.keyboards and {ecodes.KEY_A, ecodes.KEY_Z, ecodes.KEY_1, ecodes.KEY_ENTER} <= keys:
        return "keyboard"
    return "other"


def scan(settings, open_paths, learning: bool) -> list:
    """Devices not open yet, opened: [(EvdevKeypad, role)]. "other" ones only
    while learning."""
    import evdev
    found = []
    for path in evdev.list_devices():
        if path in open_paths:
            continue
        try:
            dev = evdev.InputDevice(path)
            role = role_of(dev, settings)
            if role is None or (role == "other" and not learning):
                dev.close()
                continue
            found.append((EvdevKeypad(dev, settings.grab and role != "other"), role))
        except OSError:
            continue
    return found
