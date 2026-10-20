"""The cabinet's two LED chains (buttons, accent), from the board's driver:
rpi_ws281x on a Pi 4 (pwm_chains), the kernel's PIO driver on a Pi 5
(pio_chains). Both give get().buttons / .accent with setPixelColor/show.
"""

from shared import hardware


def get():
    if hardware.board().get("led_driver") == "pio":
        from services.jb_lights import pio_chains
        return pio_chains.get()
    from services.jb_lights import pwm_chains
    return pwm_chains.get()
