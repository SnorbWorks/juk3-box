"""Both PWM LED chains through one rpi_ws281x controller.

The buttons (GPIO12, PWM channel 0) and the accent chain (GPIO13, PWM
channel 1) share the DMA engine, so they must be driven by ONE ws2811_t
with both channels set up -- two separate PixelStrips (or two programs)
fight over it. rpi_ws281x's PixelStrip always zeroes the other channel, so
this sets up the shared struct itself and hands out a small strip-like view
per chain. Every show() renders both chains (each keeps its own buffer); a
lock keeps the buttons and the accent animation from rendering at once.

    chains = pwm_chains.get()            # once per process; needs root
    chains.buttons.setPixelColor(0, c); chains.buttons.show()
"""

import atexit
import threading

from rpi_ws281x import ws

from services.jb_lights import accent, buttons

FREQ_HZ = 800000
DMA = 10


class Chain:
    """A PixelStrip-like view of one channel (setPixelColor / show)."""

    def __init__(self, owner, channel, count):
        self._owner = owner
        self._channel = channel
        self.size = count

    def setPixelColor(self, n, color):
        ws.ws2811_led_set(self._channel, n, color)

    def numPixels(self):
        return self.size

    def show(self):
        self._owner.show()


class Chains:
    def __init__(self):
        self._leds = ws.new_ws2811_t()
        self._lock = threading.Lock()
        specs = {
            0: (buttons.NUM_PIXELS, buttons.GPIO, True, ws.SK6812_STRIP_GRBW),   # !D5: inverted
            1: (accent.NUM_PIXELS, accent.GPIO, False, ws.WS2811_STRIP_GRB),
        }
        chans = {}
        for num, (count, pin, invert, strip_type) in specs.items():
            ch = ws.ws2811_channel_get(self._leds, num)
            ws.ws2811_channel_t_gamma_set(ch, list(range(256)))
            ws.ws2811_channel_t_count_set(ch, count)
            ws.ws2811_channel_t_gpionum_set(ch, pin)
            ws.ws2811_channel_t_invert_set(ch, 1 if invert else 0)
            ws.ws2811_channel_t_brightness_set(ch, 255)
            ws.ws2811_channel_t_strip_type_set(ch, strip_type)
            chans[num] = ch
        ws.ws2811_t_freq_set(self._leds, FREQ_HZ)
        ws.ws2811_t_dmanum_set(self._leds, DMA)
        resp = ws.ws2811_init(self._leds)
        if resp != 0:
            raise RuntimeError(f"ws2811_init failed: {ws.ws2811_get_return_t_str(resp)}")
        self.buttons = Chain(self, chans[0], buttons.NUM_PIXELS)
        self.accent = Chain(self, chans[1], accent.NUM_PIXELS)
        atexit.register(self.close)

    def show(self):
        with self._lock:
            if self._leds is None:
                return
            resp = ws.ws2811_render(self._leds)
        if resp != 0:
            raise RuntimeError(f"ws2811_render failed: {ws.ws2811_get_return_t_str(resp)}")

    def close(self):
        with self._lock:
            if self._leds is not None:
                ws.ws2811_fini(self._leds)
                ws.delete_ws2811_t(self._leds)
                self._leds = None


_chains = None
_get_lock = threading.Lock()


def get() -> Chains:
    """The process's one shared controller (created on first use)."""
    global _chains
    with _get_lock:
        if _chains is None:
            _chains = Chains()
        return _chains
