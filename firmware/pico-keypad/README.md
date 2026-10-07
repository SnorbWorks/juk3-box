# Pico keypad firmware

CircuitPython (10.x) for the Raspberry Pi Pico: scans the 4x5 selection
matrix, the normally closed Reset and the KY-040 encoder, and reports them as
a plain USB keyboard named "Jukebox JB Keypad". No timing or meaning lives
here -- that's `jb-input` on the Pi. Pins and key codes are in `config.py`;
wiring tables are in `Keyboard Input Build Brief.md`.

## Flash / edit

A normal boot shows only the keyboard (no drive, no serial console). To edit:

1. Unplug the Pico, **hold key A**, plug it in, keep holding ~2 s.
2. The `CIRCUITPY` drive appears. Copy `boot.py`, `code.py`, `config.py`
   (and `lib/adafruit_hid/` from the Adafruit CircuitPython bundle).
3. `code.py`/`config.py` changes reload on save; `boot.py` changes need a
   replug.

`BENCH_MODE = True` in `config.py` keeps the drive and console on every boot
and prints each event -- for wiring checks.

If the drive is ever unreachable: hold BOOTSEL while plugging in, reflash
CircuitPython, then recopy the files.

## Note on Reset

Reset is read with a `digitalio` pull-up and debounced in `code.py`, not with
`keypad.Keys(value_when_pressed=True, pull=True)`: that combination enables a
pull-down, so the normally closed switch never reads as pressed.
