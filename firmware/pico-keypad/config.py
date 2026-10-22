# Pico keypad configuration -- pins, key codes and timing only, no logic.
# The same for every cabinet: builders follow one pin diagram (README.md)
# and the jukebox's setup wizard teaches what each position means, so
# nobody edits this file.
import board
from adafruit_hid.keycode import Keycode as K

VERSION = "2.0"

# True while wiring is being verified: keeps the CIRCUITPY drive and serial
# console visible on every boot (and code.py prints each event).
BENCH_MODE = False

# --- Selection keys: a matrix of up to 6 rows x 8 columns, internal pull-ups.
# A smaller keypad uses the first rows and columns; the rest read nothing.
ROW_PINS = (board.GP5, board.GP6, board.GP7, board.GP8, board.GP15, board.GP16)
COL_PINS = (board.GP9, board.GP10, board.GP11, board.GP12, board.GP13, board.GP17, board.GP18, board.GP19)
SCAN_INTERVAL_S = 0.02  # matrix scan interval; also the debounce

# What each position sends, row by row. The first 4 rows x 5 columns are
# the original keypad's (A-K without I, then 1-0), so a cabinet wired that
# way keeps working before it's taught; every other position sends a key of
# its own for the wizard to learn.
KEY_CODES = (
    K.A, K.B, K.C, K.D, K.E, K.L, K.M, K.N,                    # row 0 (GP5)
    K.F, K.G, K.H, K.J, K.K, K.P, K.Q, K.R,                    # row 1 (GP6)
    K.ONE, K.TWO, K.THREE, K.FOUR, K.FIVE, K.S, K.T, K.U,      # row 2 (GP7)
    K.SIX, K.SEVEN, K.EIGHT, K.NINE, K.ZERO, K.V, K.W, K.X,    # row 3 (GP8)
    K.Y, K.Z, K.I, K.O, K.F1, K.F2, K.F3, K.F4,                # row 4 (GP15)
    K.F5, K.F6, K.F7, K.F8, K.F9, K.F10, K.F11, K.F12,         # row 5 (GP16)
)

# Settle times for bouncy switches, by position (row * 8 + column): ms.
# The jukebox has its own per-key settle too (jb-input); this is for a
# switch so bad it chatters faster than USB can carry.
SETTLE_MS = {}

# --- Cancel (Reset) button: normally closed to GND -> LOW at rest, HIGH when pressed ---
RESET_PIN = board.GP14
RESET_PRESSED_VALUE = True
RESET_CODE = K.ESCAPE

# --- Rotary encoder (KY-040) ---
ENC_A = board.GP2   # CLK
ENC_B = board.GP3   # DT
ENC_SW = board.GP4  # push, normally open to GND -> LOW when pressed
ENCODER_DIVISOR = 4    # counts per detent for a KY-040; the wizard calibrates the rest
ENC_CW_CODE = K.RIGHT_ARROW
ENC_CCW_CODE = K.LEFT_ARROW
ENC_PUSH_CODE = K.ENTER
MAX_TAPS_PER_LOOP = 8  # cap encoder taps per loop so a fast spin can't stall scanning

# --- Maintenance check in boot.py: key A = row 0 (GP5) x column 0 (GP9) ---
MAINT_ROW_PIN = board.GP5
MAINT_COL_PIN = board.GP9

USB_MANUFACTURER = "Jukebox"
USB_PRODUCT = "JB Keypad " + VERSION   # the jukebox reads the version from it
