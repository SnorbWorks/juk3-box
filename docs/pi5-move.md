# Moving JUKE.BOX to the Raspberry Pi 5

Prepared Oct 14, 2026, before the board arrived (dev plan roadmap item 2); the software side done Oct 20 (marked **done**). What's left needs the board on the bench. Read with [jukebox Pi 4 notes in the plan](../Virtual%20Jukebox%20—%20Development%20Plan.md).

## The short version

**Move this SD card into the Pi 5.** It already has the Pi 5's kernel (`linux-image-rpi-2712`, `kernel_2712.img`) and device trees, so it boots as-is: same OS, same services, same settings, same paired phones (Bluetooth pairings live on the card; the Pi 5's radio has a new address, so phones may need to re-pair — see Bluetooth below). A fresh install is the fallback, not the plan.

What changes after the move:

| Area | Pi 4 today | On the Pi 5 | Work |
| --- | --- | --- | --- |
| Cabinet LEDs (buttons GPIO12, accent GPIO13) | `rpi_ws281x` (PWM + DMA) | `rpi_ws281x` can't drive the RP1 I/O chip | **done**: the kernel's PIO driver (`ws2812-pio` overlays in `[pi5]`, `jb_lights/pio_chains.py`); check on the bench (below) |
| Level bar (GPIO10, SPI) | `spidev` | Same; SPI0 on GPIO10 works through RP1 | Re-check timing (the Pi 4's `core_freq=500` pin isn't needed) |
| Background video | Hardware H.264, zero-copy | No hardware H.264 (HEVC only); the CPU decodes 720p30 H.264 easily | Measure; HEVC only if Chromium uses the hardware for it |
| Memory | 1 GB; CMA cut to 256 MB, zram | 4 GB | Remove the Pi 4 squeezes later; keep module limits |
| Wi-Fi | CYW43455, brcmfmac over SDIO; hung 3 times | The **same chip and driver** (a different SD host) | jb-netwatch stays; Ethernet is still the sure fix |
| Power (through the header from the LRS-200-5) | 5 V on pins 2/4 | Same pins; without USB-PD the Pi 5 limits USB to 600 mA | **done**: `usb_max_current_enable=1` in `[pi5]` |
| Board detection | — | — | **done**: `shared/hardware.py` (`board()`: model, family, LED driver, codecs) for the setup wizard and services |

I said earlier that the Pi 5's Wi-Fi might not have the Pi 4's driver hangs. That was too hopeful: it uses the same Wi-Fi chip (CYW43455) and driver. The host side is different, so it may behave better, but jb-netwatch and the Ethernet option still matter.

## Before moving the card (on the Pi 4)

1. **Back up the card.** **done** (Oct 20): a file-level copy on the 256 GB card that was Sidecar's (Sidecar's files are in `~/Desktop/Sidecar`, a git repo). Its own partition ids (`c262cb7d`), with `cmdline.txt` and `fstab` pointing at them, so it boots on its own; checked file by file against the live card; the overflow swap file recreated. Not boot-tested.
2. **Fence the Pi-4-only boot settings** in `/boot/firmware/config.txt`. **done** (Oct 20; the old one is `config.txt.bak-20261020`; the Pi 4 rebooted on it with `core_freq=500` still in force):

   ```ini
   [pi4]
   # the level bar's SPI clock follows the core clock on a Pi 4
   core_freq=500
   core_freq_min=500

   [pi5]
   dtoverlay=nospi10
   usb_max_current_enable=1
   dtoverlay=ws2812-pio,gpio=12,num_leds=23,rgbw,brightness=0,dev_name=leds-buttons
   dtoverlay=ws2812-pio,gpio=13,num_leds=349,brightness=0,dev_name=leds-accent

   [all]
   dtparam=spi=on
   ```

   `dtoverlay=vc4-kms-v3d,cma-256` stays for both (256 MB of a Pi 5's 4 GB is fine; the Pi 5 needs vc4-kms-v3d too). `cgroup_enable=memory` in `cmdline.txt` stays.
3. **Set the supply to 5.2–5.25 V** at the board (already the plan) and keep the Pi branch fuse in mind: a Pi 5 under load with the keypad and DAC draws more than a Pi 4.
4. **Fit the Active Cooler.** A Pi 5 in a closed cabinet needs it.

## On the Pi 5, in order

1. Boot; `vcgencmd get_throttled` must read `0x0` (and `vcgencmd pmic_read_adc` for the supply under load).
2. `systemctl --failed` empty. `ls /dev/leds-*` shows `leds-buttons` and `leds-accent`; `jb-lights` logs "GPIO12 inverted for the buttons (CTRL … -> …)" and the cabinet lights come on (bench checks under Work items, 1).
3. Audio: the DAC Pro is found from its HAT EEPROM, as on the Pi 4 (`aplay -l`), and a song plays. `admin` must be in the `pipewire` group (`sudo usermod -aG pipewire admin`, then reboot) so PipeWire runs realtime (`ps -eLo cls,rtprio,comm | grep data-loop` shows `FF`); without it the DAC runs dry under load (pops and cut-outs, Oct 20). jb-audio's own PipeWire gets it from `LimitRTPRIO` in its unit.
4. Bluetooth: the Pi 5's radio has its own address, so phones paired to the Pi 4 see a new device. Expect to pair the iPhone again (Settings > Bluetooth > Pair a new phone), then Forget the old entry (the old library is kept per phone; forgetting deletes it, so forget the old one only after the new one has synced).
5. Kiosk: boot to splash to Home; background video smooth? `top` while it plays (target: well under one core).
6. Re-measure what was tuned on the Pi 4: knob turns, Home ↔ All Songs, page flips, boot time (the numbers are in the Neon theme notes and the plan).
7. Then the work items below.

## Work items

1. **LED driver for the Pi 5.** **Built** (Oct 20), not yet run on a Pi 5. The card's Pi 5 kernel has a WS281x driver of its own on the PIO block (`ws2812-pio-rp1`, overlay `ws2812-pio`): no library to install. Each chain is an overlay in `[pi5]` and a device (`/dev/leds-buttons`, `/dev/leds-accent`); `jb_lights/pio_chains.py` writes whole frames to them (4 bytes per LED, R G B W, whatever the strip; the driver puts them in wire order), with `brightness=0` (its pass-through: no gamma, jb-lights does its own). `chains.py` picks it or rpi_ws281x by `shared/hardware.board()["led_driver"]`. Bench checks:
   - **The button chain's inverted signal.** The driver has no invert, so `pio_chains` inverts GPIO12's output in RP1 itself (OUTOVER = 1 in the pin's GPIO_CTRL, through `/dev/gpiomem0`; logged with the register before and after). If the buttons show garbage, that's the first suspect: check the register with the RP1 datasheet (OUTOVER is bits 13:12), or fall back to the next board revision's footprint fix (planned: G, D5, !D5, C5).
   - **Both chains at once** at the shows' frame rates (23 GRBW + 349 GRB: about 11 ms per accent frame on the wire).
   - **The level bar on SPI** (GPIO10): the Pi 5's SPI clock comes from RP1, not the core clock, so `[pi4]`'s pin doesn't apply; 2.4 MHz rounds to about 2.38 MHz there, within WS2812B timing. Look for flicker.
   - Buttons aren't parked with `pinctrl` on close on a Pi 5 (that would take the pin away from PIO until a reboot).
2. **Background video.** Try the H.264 720p30 copy as is (software decode). If the CPU cost is high, add an HEVC profile to `services/jb_ui/media.py` for the Pi 5 and check whether Chromium uses the HEVC hardware (the stateless V4L2 HEVC decoder is upstream; Chromium's use of it on the Pi isn't confirmed). The kiosk's `AcceleratedVideoDecodeLinux*` flags are for the Pi 4's H.264 block.
3. **Hardware in setup.** The first-boot wizard (roadmap item 5) shows `board()`'s answer for the owner to confirm, then applies that board's settings itself (boot config, LED driver, video profile); the services read it for their settings. A new owner has no one to do this by hand, so every step this guide does manually must end up in the wizard's code.
4. **Undo the Pi 4 squeezes** once the Pi 5 is the reference: CMA, the zram size, the decision notes in the Neon theme ("measured on the Pi 4").

## Sources

- [Adafruit: Using NeoPixels on the Pi 5 and Pi 500](https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/using-neopixels-on-the-pi-5)
- [Hackster: userspace WS281x on the Raspberry Pi 5](https://hackster.io/news/userspace-ws281x-control-on-the-raspberry-pi-5-inches-closer-with-new-python-library-release-6c8af3e50d9e)
- [rpi5-ws2812 (SPI)](https://pypi.org/project/rpi5-ws2812/0.1.1), [Pi5Neo (SPI)](https://pypi.org/project/Pi5Neo/1.0.4)
- [Jeff Geerling: Can the Raspberry Pi 5 handle 4K?](https://www.jeffgeerling.com/blog/2024/can-raspberry-pi-5-handle-4k/) (H.264 in software, HEVC in hardware)
- [Phoronix: Raspberry Pi HEVC decoder driver](https://www.phoronix.com/news/Raspberry-Pi-HEVC-H265-Decode)
- [Raspberry Pi: USB power delivery on the Raspberry Pi 5](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009856-WP-1-USB%20Power%20delivery%20on%20Raspberry%20Pi%205.pdf), [bret.dk: powering the Pi 5](https://bret.dk/?p=7113) (`usb_max_current_enable=1` when powered through the header)
