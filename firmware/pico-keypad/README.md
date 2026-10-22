# Pico keypad program

CircuitPython (10.x) for the Raspberry Pi Pico: scans the selection-button
matrix, the normally closed Cancel button and the KY-040 knob, and reports
them as a plain USB keyboard named "Jukebox JB Keypad 2.0" (the version is in
the name). No timing or meaning lives here: that's `jb-input` on the Pi, and
the setup wizard teaches it what each button is. So every cabinet runs this
same program, and nobody edits it.

## Installing

The jukebox does it, from the setup wizard's controls step (or Admin › Setup ›
Teach the buttons again):

- **A new Pico:** hold its white BOOTSEL button while you plug it into the
  jukebox. The wizard sees it ("Found a keypad board with nothing on it") and
  installs CircuitPython, the HID library and this program in about a minute.
  Both are bundled in `vendor/` (MIT), so nothing is downloaded.
- **An older version of this program:** from 2.0 on, the wizard asks it to
  restart with its drive showing (it flashes the keyboard lights in a
  pattern, `jbsignal.py`) and copies the new version over. Version 1 (before
  the version was in the name) needs it done by hand once: unplug it, hold A,
  plug it back in.
- **A Pico with something else on it** (maybe your own project): the wizard
  asks before replacing it.

By hand: copy `boot.py`, `code.py`, `config.py`, `jbsignal.py` and
`vendor/lib/adafruit_hid/` (into `lib/`) to the `CIRCUITPY` drive.

## Wiring

| What | Pico pins |
|---|---|
| Selection matrix rows (up to 6) | GP5, GP6, GP7, GP8, GP15, GP16 |
| Selection matrix columns (up to 8) | GP9, GP10, GP11, GP12, GP13, GP17, GP18, GP19 |
| Cancel button, normally closed, to GND | GP14 |
| Knob (KY-040): CLK, DT, SW | GP2, GP3, GP4 |
| Ground | any GND pin |

Each selection button joins one row to one column: up to 48 buttons, no diodes
needed for one button at a time. Use the first rows and columns for a smaller
keypad; wire them in any order, since the wizard learns which is which. A
100-selection keypad (10 letters, 10 numbers) wired like the original
(letters A–E on GP5, F–K on GP6, 1–5 on GP7, 6–0 on GP8, across GP9–GP13)
sends those letters and numbers before it's taught.

## Maintenance mode

A normal boot shows only the keyboard (no drive, no serial console). The
drive shows when:

1. the jukebox asks (the keyboard-light signal), for one boot; or
2. you hold key A while plugging it in.

`BENCH_MODE = True` in `config.py` keeps the drive and console on every boot
and prints each event, for checking the wiring.

If the drive is ever unreachable: hold BOOTSEL while plugging in; the wizard
installs everything again.

## Note on Cancel

Cancel is read with a `digitalio` pull-up and debounced in `code.py`, not with
`keypad.Keys(value_when_pressed=True, pull=True)`: that combination enables a
pull-down, so the normally closed switch never reads as pressed.
