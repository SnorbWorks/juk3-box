# Bundled for the keypad installer

The jukebox installs these on a blank Raspberry Pi Pico (jb-setupctl's
`keypad` command, from the setup wizard's controls step), so nothing is
downloaded at the cabinet.

| File | What | Version | License |
|---|---|---|---|
| `adafruit-circuitpython-raspberry_pi_pico-en_US-10.3.1.uf2` | CircuitPython for the Pico | 10.3.1 | MIT (`CircuitPython-LICENSE`) |
| `lib/adafruit_hid/` (`__init__`, `keyboard`, `keycode`) | Adafruit CircuitPython HID, the parts the keypad uses | 6.1.10 | MIT (`lib/adafruit_hid/LICENSE`) |

From https://circuitpython.org/board/raspberry_pi_pico/ and
https://github.com/adafruit/Adafruit_CircuitPython_HID. To move to newer
ones, replace the files, update this table and `UF2` in deploy/jb-setupctl.
