# What Juk3box is built from

Juk3box's own code is GPL-3.0 (`LICENSE`). It's built on the free software below, each
under its own license. Versions are those on the first Pi 5 cabinet (October 2026).
`tools/check_updates.py` compares the pinned and bundled ones with their newest releases
every month (`.github/workflows/check-updates.yml`).

## Bundled in this repository

| What | Version | License | Where |
|---|---|---|---|
| CircuitPython (for the Pico keypad board) | 10.3.1 | MIT | `firmware/pico-keypad/vendor/` |
| Adafruit CircuitPython HID | 6.1.10 | MIT | `firmware/pico-keypad/vendor/lib/adafruit_hid/` |
| MQTT.js (the screen's link to the jukebox) | 5.16.0 | MIT | `services/jb_ui/engine/mqtt-5.16.0.min.js` |
| Lucide icons (Iconify set) | set of Oct 9, 2026 | ISC | `services/jb_ui/icons/` |
| YAMNet sound model (Google), its weights converted to numpy by `tools/yamnet_convert.py` (the batch norms folded into the convolutions), and its class map | as published, Oct 2026 | Apache-2.0 | `services/jb_analyzer/models/` |
| Fonts: Bebas Neue, Courier Prime, Inter, Nunito, Oswald, Playfair Display, Righteous | Oct 2026 | SIL Open Font License 1.1 | `services/jb_ui/engine/fonts/` |
| The Neon theme's concert video and pictures | | Made for this project; covered by its license | `themes/neon/assets/` |

Each one's license text is next to it.

## Python packages (installed on the jukebox)

Listed in `deploy/requirements.txt`, pinned in `deploy/install/constraints.txt`.

| Package | Version | License |
|---|---|---|
| aiohttp | 3.14.3 | Apache-2.0 and MIT |
| aiomqtt | 2.5.1 | BSD-3-Clause |
| dbus-next | 0.2.3 | MIT |
| evdev | 2.0.0 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause (and others, all permissive) |
| paho-mqtt | 2.1.0 | EPL-2.0 or BSD-3-Clause |
| pillow | 12.3.0 | MIT-CMU |
| rpi_ws281x | 5.0.0 | MIT |
| segno | 1.6.6 | BSD-3-Clause |
| tinytag | 2.3.2 | MIT |

## From Raspberry Pi OS (installed on the jukebox)

Installed by `deploy/install/install.sh`, updated by Raspberry Pi OS, not by this repository.

| Program | Does | Version | License |
|---|---|---|---|
| Raspberry Pi OS (Debian 13) and its Linux kernel | The operating system | | GPL-2.0 and others |
| Mosquitto | The message bus between the services | 2.0.21 | EPL-2.0 or EDL-1.0 |
| PipeWire, WirePlumber | Sound | 1.4.2, 0.5.8 | MIT, LGPL-2.1+ |
| BlueZ | Bluetooth (phones) | 5.82 | GPL-2.0+, LGPL-2.1+ |
| Shairport Sync | AirPlay receiver | 4.3.7 | MIT |
| Chromium | Shows the song board | 152 | BSD-3-Clause and others |
| Sway, seatd | The screen's window manager | 1.10.1, 0.9.1 | MIT, LGPL-2.1+ |
| FFmpeg, mpv | Video and audio decoding | 7.1.5, 0.40.0 | LGPL-2.1+ |
| grim | Screenshots of the screen | 1.4.0 | MIT |
| Noto CJK fonts | Song titles in Chinese, Japanese and Korean | 2024-07-30 | SIL Open Font License 1.1 |

These are installed from Raspberry Pi OS's own packages, unchanged. Their sources and
license texts are on every jukebox (`/usr/share/doc/<package>/copyright`) and from
Raspberry Pi and Debian.
