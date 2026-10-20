import struct

from rpi_ws281x import Color

from services.jb_lights import accent, buttons, pio_chains


def make(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    for name in (pio_chains.BUTTONS_DEV, pio_chains.ACCENT_DEV):
        (dev / name).write_bytes(b"")
    gpiomem = tmp_path / "gpiomem0"
    regs = bytearray(4096)
    # GPIO12's CTRL as the PIO driver leaves it: FUNCSEL = PIO (7), F_M = 4.
    struct.pack_into("<I", regs, pio_chains.ctrl_offset(12), (4 << 5) | 7)
    gpiomem.write_bytes(bytes(regs))
    return dev, gpiomem


def ctrl(gpiomem, gpio):
    return struct.unpack_from("<I", gpiomem.read_bytes(), pio_chains.ctrl_offset(gpio))[0]


def test_both_chains_write_rgbw_frames_and_the_button_pin_is_inverted(tmp_path):
    dev, gpiomem = make(tmp_path)
    assert pio_chains.available(dev)
    chains = pio_chains.Chains(dev, gpiomem, log=lambda *_: None)
    chains.buttons.setPixelColor(0, Color(10, 20, 30, 40))
    chains.buttons.setPixelColor(buttons.NUM_PIXELS, Color(1, 1, 1))   # past the end: ignored
    chains.buttons.show()
    chains.accent.setPixelColor(2, Color(255, 128, 0))
    chains.accent.show()
    frame = (dev / pio_chains.BUTTONS_DEV).read_bytes()
    assert len(frame) == 4 * buttons.NUM_PIXELS and frame[:4] == bytes((10, 20, 30, 40))
    frame = (dev / pio_chains.ACCENT_DEV).read_bytes()
    assert len(frame) == 4 * accent.NUM_PIXELS and frame[8:12] == bytes((255, 128, 0, 0))
    # OUTOVER = invert; the function and filter bits left alone.
    assert ctrl(gpiomem, 12) == (1 << 12) | (4 << 5) | 7
    assert ctrl(gpiomem, 13) == 0


def test_buttons_are_not_parked_with_pinctrl_on_a_pi5(tmp_path, monkeypatch):
    dev, gpiomem = make(tmp_path)
    chains = pio_chains.Chains(dev, gpiomem, log=lambda *_: None)
    ran = []
    monkeypatch.setattr(buttons.subprocess, "run", lambda *a, **k: ran.append(a))
    buttons.close_strip(chains.buttons)
    assert ran == []
    assert (dev / pio_chains.BUTTONS_DEV).read_bytes() == bytes(4 * buttons.NUM_PIXELS)


def test_missing_overlays_mean_not_available(tmp_path):
    assert not pio_chains.available(tmp_path)
