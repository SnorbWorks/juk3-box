# Extensions inventory

Everything JUKE.BOX runs or ships that isn't its own code: other people's software, fonts, icons, web services and pictures. The list itself is [deploy/extensions.json](../deploy/extensions.json); the admin's **Extensions** page shows it with the versions installed on the jukebox. Add an entry whenever something new comes in.

*Extensions* here are not *modules*. A module is an add-on people write and install to give JUKE.BOX new features or new ways to use it (Weather, Weather Mix); its contracts are [contracts/modules.md](contracts/modules.md) and [contracts/installed-modules.md](contracts/installed-modules.md). An extension is someone else's software the system needs at a deeper level, with a license that asks something of us.

Inventory taken Oct 14, 2026, from the reference Pi 4 (Raspberry Pi OS, Debian 13) and the repo. Not legal advice: a lawyer should look over the "before selling" items.

## What each license asks

| Duty | Licenses | What JUKE.BOX has to do |
| --- | --- | --- |
| Keep the notice | MIT, BSD, ISC, Apache-2.0, PSF, OFL (fonts), EPL/EDL | Ship each one's copyright and license text: an About / Licenses screen or file in the product |
| Offer source | GPL, LGPL (Linux, BlueZ, NetworkManager, systemd, FFmpeg) | If JUKE.BOX ships as an SD image or device, offer the source of those packages for the exact versions shipped (a written offer, valid 3 years, or the Raspberry Pi OS / Debian source alongside) |
| Needs an agreement | Last.fm, Open-Meteo | An agreement or paid plan before commercial use |
| To confirm | Apple artwork, theme pictures | Rights or terms to check before selling |

**JUKE.BOX's own code stays its own.** Its services are separate programs that talk to the GPL parts over the message bus, D-Bus (BlueZ) or by running them (FFmpeg, nmcli). None of its Python imports a GPL library (the Python dependencies are MIT, BSD, Apache, EPL/EDL, MIT-CMU). That's the usual line for not being a "derivative work" of GPL software. Keep it that way: check the license before adding a library.

## Before selling: to settle

1. **Last.fm API** (song popularity for Top 100, mood tags for the music lights, Weather Mix). Its terms allow non-commercial use only without a separate agreement, and ask for attribution. Options: an agreement with Last.fm; another source; or Last.fm only with the owner's own API key (the owner then holds the terms, as with Weather Mix's key field today).
2. **Open-Meteo** (Weather, the Weather location search). The free plan is non-commercial; a product needs their paid plan (or the owner's own key). Attribution ("Weather data by Open-Meteo.com", CC BY 4.0) is required either way. **Done** (Oct 14): the Weather block shows "Weather data by Open-Meteo.com" (module 1.2.1), and the admin's location search credits it too.
3. **Apple iTunes Search API** (covers, and the artist/album/genre guesses). Free and keyless, but Apple's terms see the artwork as promoting the music on Apple's store. Using it in a product sold needs a read of Apple Media Services terms; the alternative is covers from the phone or another source.
4. **Theme pictures and video** (Neon's background picture and video, logo; the genre and browse pictures in Images/). Where they came from isn't recorded. Confirm the rights (made by you, licensed stock, or generated with a service whose terms allow commercial use) and note it in deploy/extensions.json.
5. **The themes' own license.** Both themes' theme.json say `"license": "GPL-3.0"` (copied from WordPress's convention). For themes you sell, that would let anyone copy and resell them. Decide on purpose: GPL, a permissive license, or your own terms.
6. **A Licenses screen.** The "keep the notice" duty needs the license texts in the product. The admin's Extensions page lists the licenses; the full texts (fonts, Lucide, MQTT.js are in the repo) should be readable there too, and on the cabinet's Settings.

## Notes

- **Pi 5:** `rpi_ws281x` doesn't run on the Pi 5; its replacement (an SPI or PIO LED driver) gets an entry when chosen.
- **Development only:** pytest and grim are listed as *dev*; nothing in the running jukebox needs them.
- **Keypad firmware:** CircuitPython and Adafruit's HID library (MIT) run on the keypad's Pico; the same notice duty applies if the keypad ships.
- **Not listed:** Raspberry Pi OS packages JUKE.BOX doesn't use directly (the inventory lists what it relies on). An SD image would ship the whole OS: the "offer source" duty then covers every GPL package in it, which the Raspberry Pi OS source archive answers.
