# USB identity, keyboard-only HID, and the maintenance-mode check.
import digitalio
import storage
import supervisor
import usb_cdc
import usb_hid

import config

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


if not config.BENCH_MODE and not key_a_held():
    # Normal boot: the Pi should see a keyboard and nothing else.
    storage.disable_usb_drive()
    usb_cdc.disable()
