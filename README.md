# Juk3box

A jukebox built on a Raspberry Pi: a song board on a screen, a knob and keypad, light shows that follow the music, and your music from a phone (Bluetooth or the Juk3box Helper app), a USB drive or AirPlay.

## Make one

You need a Raspberry Pi 5 or 4, a microSD card (16 GB or more), and a computer to write the card.

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Get Juk3box into Imager, either way:
   - **From Imager's list:** open Imager's app options, set **Content repository** to
     `https://github.com/SnorbWorks/juk3-box/releases/latest/download/imager.json`, and choose **Juk3box** as the operating system.
   - **Or download** `juk3box-….img.xz` from the [newest release](https://github.com/SnorbWorks/juk3-box/releases/latest) and choose **Use custom** in Imager.
3. Write the card. Skip Imager's settings (name, Wi-Fi, users): the jukebox asks for everything itself.
4. Put the card in the Pi and start it. After a minute the screen shows **Juk3box Setup** with a Wi-Fi name and a QR code.
5. On your phone, scan the code (or open the Juk3box Helper app and tap **Set up a new jukebox**). The setup pages walk you through the rest: a password, a name, your Wi-Fi, the hardware checks, your music.

With a network cable plugged in, the screen shows an address to open instead of the Wi-Fi.

## Updates

A jukebox checks for a new release each night and installs it (Admin › Setup › Updates, where it can also be turned off or run now). Updates are only installed if they're signed with this project's key; if a new version doesn't start properly, the jukebox goes back to the one it had.

## Last.fm

Setup asks for your own free Last.fm API key. It fills the board's Top 100 and lets the light show match each song's genre. You can skip it and add it later on the admin's Setup page.

## For developers

The jukebox is a set of small Python services talking over MQTT (`services/`), a browser-based screen (`services/jb_ui`), and an admin web app (`services/jb_admin`, port 8080). `deploy/deploy.sh <service>` sends a working copy to a Pi; `deploy/install/install.sh` installs everything on a fresh Raspberry Pi OS Lite; `deploy/image/build-image.sh` makes the card image, and `deploy/release.sh v1.2.3` publishes a release (GitHub builds it). Docs are in `docs/`.

## License

GPL-3.0 (see LICENSE). Fonts carry their own licenses (SIL Open Font License, next to each font). The Neon theme's concert video and picture were made for this project with open models (Z-Image-Turbo and Wan 2.2, both Apache-2.0) and are covered by the project's license; `themes/neon/assets/CREDITS.md` says how.
