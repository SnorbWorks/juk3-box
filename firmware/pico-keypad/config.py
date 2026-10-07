# Pico keypad configuration -- pins, key codes and timing only, no logic.
# See "Keyboard Input Build Brief.md" for the wiring tables these come from.
import board
from adafruit_hid.keycode import Keycode

# True while wiring is being verified: keeps the CIRCUITPY drive and serial
# console visible on every boot (and code.py prints each event). Set False
# once all bench checks pass -- then a normal boot shows only a keyboard and
# holding key A at power-up is the way into maintenance mode.
BENCH_MODE = False

# --- Selection keys: 4 x 5 matrix, internal pull-ups, no diodes ---
ROW_PINS = (board.GP5, board.GP6, board.GP7, board.GP8)
COL_PINS = (board.GP9, board.GP10, board.GP11, board.GP12, board.GP13)
SCAN_INTERVAL_S = 0.02  # matrix scan interval; also the debounce

# Key number = row * 5 + column. Letters skip I; digits run 1-9 then 0.
KEY_CODES = (
    Keycode.A, Keycode.B, Keycode.C, Keycode.D, Keycode.E,                  # row 0
    Keycode.F, Keycode.G, Keycode.H, Keycode.J, Keycode.K,                  # row 1
    Keycode.ONE, Keycode.TWO, Keycode.THREE, Keycode.FOUR, Keycode.FIVE,    # row 2
    Keycode.SIX, Keycode.SEVEN, Keycode.EIGHT, Keycode.NINE, Keycode.ZERO,  # row 3
)
KEY_NAMES = ("A", "B", "C", "D", "E", "F", "G", "H", "J", "K",
             "1", "2", "3", "4", "5", "6", "7", "8", "9", "0")

# --- Reset button: normally closed to GND -> LOW at rest, HIGH when pressed ---
# Read with an internal pull-UP and debounced in code.py (not keypad.Keys --
# see ResetButton there for why).
RESET_PIN = board.GP14
RESET_PRESSED_VALUE = True
RESET_CODE = Keycode.ESCAPE

# --- Rotary encoder (KY-040) ---
ENC_A = board.GP2   # CLK
ENC_B = board.GP3   # DT
ENC_SW = board.GP4  # push, normally open to GND -> LOW when pressed
ENCODER_DIVISOR = 4    # counts per detent; some KY-040s need 2
ENCODER_INVERT = False
ENC_CW_CODE = Keycode.RIGHT_ARROW
ENC_CCW_CODE = Keycode.LEFT_ARROW
ENC_PUSH_CODE = Keycode.ENTER
MAX_TAPS_PER_LOOP = 8  # cap encoder taps per loop so a fast spin can't stall scanning

# --- Maintenance check in boot.py: key A = row 0 (GP5) x column 0 (GP9) ---
MAINT_ROW_PIN = board.GP5
MAINT_COL_PIN = board.GP9

USB_MANUFACTURER = "Jukebox"
USB_PRODUCT = "JB Keypad"
