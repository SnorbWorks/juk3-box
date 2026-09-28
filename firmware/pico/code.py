import time
import board
import digitalio
import rotaryio
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

kbd = Keyboard(usb_hid.devices)

# CLK, DT
encoder = rotaryio.IncrementalEncoder(board.GP2, board.GP3)
last_position = encoder.position

sw = digitalio.DigitalInOut(board.GP4)
sw.direction = digitalio.Direction.INPUT
sw.pull = digitalio.Pull.UP
sw_last_state = True

print("Encoder test ready.")

while True:
    position = encoder.position
    if position != last_position:
        delta = position - last_position
        last_position = position
        if delta > 0:
            kbd.send(Keycode.RIGHT_ARROW)
            print("CW  ->", delta, "RIGHT_ARROW")
        else:
            kbd.send(Keycode.LEFT_ARROW)
            print("CCW ->", delta, "LEFT_ARROW")

    sw_state = sw.value
    if sw_state != sw_last_state:
        sw_last_state = sw_state
        if sw_state is False:
            kbd.send(Keycode.ENTER)
            print("CLICK -> ENTER")

    time.sleep(0.005)
