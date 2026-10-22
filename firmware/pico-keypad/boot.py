# USB identity, keyboard-only HID, and the maintenance-mode check.
import digitalio
import microcontroller
import storage
import usb_cdc
import usb_hid
import supervisor

import config

MAINTENANCE_FLAG = 0x4A   # in nvm[0]: the jukebox asked for maintenance mode (code.py)

supervisor.set_usb_identification(manufacturer=config.USB_MANUFACTURER, product=config.USB_PRODUCT)
usb_hid.enable((usb_hid.Device.KEYBOARD,))


def key_a_held():
    # keypad doesn't exist yet in boot.py, so read key A directly: drive its
    # row low and look for its column pulled low through the switch. (Never
    # use Reset for this -- it's normally closed, so a disconnected wire would
    # look like a held button on every boot.)
    row = digitalio.DigitalInOut(config.MAINT_ROW_PIN)
    row.switch_to_output(value=False)
    col = digitalio.DigitalInOut(config.MAINT_COL_PIN)
    col.switch_to_input(pull=digitalio.Pull.UP)
    held = not col.value
    row.deinit()
    col.deinit()
    return held


asked = microcontroller.nvm[0] == MAINTENANCE_FLAG
if asked:
    microcontroller.nvm[0] = 0    # once: the next restart is a normal one

if not config.BENCH_MODE and not asked and not key_a_held():
    # Normal boot: the Pi should see a keyboard and nothing else.
    storage.disable_usb_drive()
    usb_cdc.disable()
