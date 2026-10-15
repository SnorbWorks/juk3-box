# Moving JUKE.BOX to the Raspberry Pi 5

Prepared Oct 14, 2026, before the board arrived (dev plan roadmap item 2). Nothing here has been applied to the jukebox except where marked **done**. Read with [jukebox Pi 4 notes in the plan](../Virtual%20Jukebox%20—%20Development%20Plan.md).

## The short version

**Move this SD card into the Pi 5.** It already has the Pi 5's kernel (`linux-image-rpi-2712`, `kernel_2712.img`) and device trees, so it boots as-is: same OS, same services, same settings, same paired phones (Bluetooth pairings live on the card; the Pi 5's radio has a new address, so phones may need to re-pair — see Bluetooth below). A fresh install is the fallback, not the plan.

What changes after the move:

| Area | Pi 4 today | On the Pi 5 | Work |
| --- | --- | --- | --- |
| Cabinet LEDs (buttons GPIO12, accent GPIO13) | `rpi_ws281x` (PWM + DMA) | `rpi_ws281x` can't drive the RP1 I/O chip | **New driver** (below). jb-lights stays idle on a Pi 5 until then (**done**: it detects the board and doesn't crash-loop) |
| Level bar (GPIO10, SPI) | `spidev` | Same; SPI0 on GPIO10 works through RP1 | Re-check timing (the Pi 4's `core_freq=500` pin isn't needed) |
| Background video | Hardware H.264, zero-copy | No hardware H.264 (HEVC only); the CPU decodes 720p30 H.264 easily | Measure; HEVC only if Chromium uses the hardware for it |
| Memory | 1 GB; CMA cut to 256 MB, zram | 4 GB | Remove the Pi 4 squeezes later; keep module limits |
| Wi-Fi | CYW43455, brcmfmac over SDIO; hung 3 times | The **same chip and driver** (a different SD host) | jb-netwatch stays; Ethernet is still the sure fix |
| Power (through the header from the LRS-200-5) | 5 V on pins 2/4 | Same pins; without USB-PD the Pi 5 limits USB to 600 mA | `usb_max_current_enable=1` |
| Board detection | — | — | **done**: `shared/hardware.py` (`board()`: model, family, LED driver, codecs) for the setup wizard and services |

I said earlier that the Pi 5's Wi-Fi might not have the Pi 4's driver hangs. That was too hopeful: it uses the same Wi-Fi chip (CYW43455) and driver. The host side is different, so it may behave better, but jb-netwatch and the Ethernet option still matter.

## Before moving the card (on the Pi 4)

1. **Back up the card** (`rpi-clone`, as for Sidecar). Remember the Sidecar lesson: rpi-clone doesn't fix `root=PARTUUID=` in the copy's `cmdline.txt`; check it by hand.
2. **Fence the Pi-4-only boot settings** in `/boot/firmware/config.txt` (proposed, not applied — it's the boot config):

   ```ini
   # (everything above [cm4] stays, minus the two core_freq lines at the end)
   [pi4]
   # Fixed core clock: the level bar's SPI timing on the Pi 4 depends on it.
   core_freq=500
   core_freq_min=500
   [pi5]
   dtoverlay=nospi10
   # Powered through the header (no USB-PD): allow full USB current.
   usb_max_current_enable=1
   [all]
   dtparam=spi=on
   ```

   `dtoverlay=vc4-kms-v3d,cma-256` can stay for both (256 MB of a Pi 5's 4 GB is fine; the Pi 5 needs vc4-kms-v3d too). `cgroup_enable=memory` in `cmdline.txt` stays.
3. **Set the supply to 5.2–5.25 V** at the board (already the plan) and keep the Pi branch fuse in mind: a Pi 5 under load with the keypad and DAC draws more than a Pi 4.
4. **Fit the Active Cooler.** A Pi 5 in a closed cabinet needs it.

## On the Pi 5, in order

1. Boot; `vcgencmd get_throttled` must read `0x0` (and `vcgencmd pmic_read_adc` for the supply under load).
2. `systemctl --failed` empty; `jb-lights` logs that it's waiting for the Pi 5 driver.
3. Audio: the DAC Pro is found from its HAT EEPROM, as on the Pi 4 (`aplay -l`), and a song plays.
4. Bluetooth: the Pi 5's radio has its own address, so phones paired to the Pi 4 see a new device. Expect to pair the iPhone again (Settings > Bluetooth > Pair a new phone), then Forget the old entry (the old library is kept per phone; forgetting deletes it, so forget the old one only after the new one has synced).
5. Kiosk: boot to splash to Home; background video smooth? `top` while it plays (target: well under one core).
6. Re-measure what was tuned on the Pi 4: knob turns, Home ↔ All Songs, page flips, boot time (the numbers are in the Neon theme notes and the plan).
7. Then the work items below.

## Work items

1. **LED driver for the Pi 5.** The best fit is the Pi 5's PIO block through the kernel's `/dev/pio0`: Adafruit's `Adafruit-Blinka-Raspberry-Pi5-Neopixel` drives WS281x on any GPIO, so the buttons can stay on GPIO12 and the accent strip on GPIO13 ([Adafruit's guide](https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/using-neopixels-on-the-pi-5)). Needs a udev rule for `/dev/pio0` (`SUBSYSTEM=="*-pio", GROUP="gpio", MODE="0660"`), a current kernel and firmware, and two checks on the bench:
   - **The button chain's inverted signal.** Today rpi_ws281x inverts it in software (the board's Pixel Shifter footprint; Rev E's U2 rework). The PIO library has no invert option documented. Either the next board revision fixes the footprint (already planned: G, D5, !D5, C5) or a small custom PIO program inverts it.
   - **Both chains at once** (23 GRBW + 349 GRB pixels) at the frame rates the shows use.
   SPI-only libraries (`rpi5-ws2812`, `Pi5Neo`) exist too, but use GPIO10 (the level bar's) — a rewiring, so the fallback.
   jb-lights already isolates the hardware (`pwm_chains.py` gives each chain a PixelStrip-like view): the Pi 5 driver is a second implementation of that view, picked by `shared/hardware.board()["led_driver"]`.
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
