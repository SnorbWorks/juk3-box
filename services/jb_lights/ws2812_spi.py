"""WS2812B driver over the Pi's SPI MOSI line (GPIO10), via /dev/spidev0.0.

WS2812B pixels read a single-wire, timing-encoded bitstream. Clocking SPI at
2.4 MHz makes each SPI bit ~417 ns, so every WS2812 bit can be sent as three
SPI bits: 1 -> 110 (~833 ns high), 0 -> 100 (~417 ns high), both within the
WS2812B's tolerance. No root, no DMA -- the admin user only needs the `spi`
group. Requires dtparam=spi=on plus core_freq=500 / core_freq_min=500 in
config.txt on a Pi 4 (the SPI clock divides the VPU core clock).
"""

import spidev

SPI_HZ = 2_400_000
# >= 280 us of low line latches the frame; 2.4 MHz * 300 us = 720 bits = 90 bytes.
RESET_BYTES = bytes(90)
# Rough WS2812B draw: ~20 mA per color channel at full (255) brightness.
AMPS_PER_CHANNEL_FULL = 0.020


def _encode_byte(value: int) -> bytes:
    bits = 0
    for i in range(7, -1, -1):
        bits = (bits << 3) | (0b110 if (value >> i) & 1 else 0b100)
    return bits.to_bytes(3, "big")


_LUT = [_encode_byte(v) for v in range(256)]


class WS2812SPI:
    """A chain of GRB WS2812B pixels on one SPI bus.

    show() takes (r, g, b) tuples (0-255) and applies, in order:
      1. brightness -- a global scale factor (0.0-1.0)
      2. the current limiter -- if the estimated draw exceeds max_amps, the
         whole frame is scaled down uniformly so it doesn't
    """

    def __init__(self, num_pixels: int, brightness: float = 1.0, max_amps: float | None = None,
                 bus: int = 0, device: int = 0):
        self.num_pixels = num_pixels
        self.brightness = brightness
        self.max_amps = max_amps
        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = SPI_HZ
        self._spi.mode = 0

    def estimate_amps(self, pixels) -> float:
        return sum(r + g + b for r, g, b in pixels) / 255 * AMPS_PER_CHANNEL_FULL

    def show(self, pixels) -> float:
        """Sends one frame; returns the estimated current draw in amps."""
        pixels = list(pixels)[: self.num_pixels]
        pixels += [(0, 0, 0)] * (self.num_pixels - len(pixels))
        scale = max(0.0, min(1.0, self.brightness))
        if self.max_amps is not None:
            amps = self.estimate_amps(pixels) * scale
            if amps > self.max_amps:
                scale *= self.max_amps / amps
        out = bytearray()
        for r, g, b in pixels:
            for c in (g, r, b):  # WS2812B wire order is GRB
                out += _LUT[int(c * scale + 0.5)]
        out += RESET_BYTES
        self._spi.writebytes2(out)
        return self.estimate_amps(pixels) * scale

    def off(self) -> None:
        self.show([])

    def close(self) -> None:
        self._spi.close()
