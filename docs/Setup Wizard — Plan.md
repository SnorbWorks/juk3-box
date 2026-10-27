# Setup Wizard — Plan

Oct 20, 2026 · the owner

The first-boot setup wizard (dev plan roadmap item 5, Phase 7b), in one place. It merges the helper app plan's Phase 6 (hotspot onboarding), pi5-move.md's work item 3 (hardware in setup, how the Pi 5 is powered) and the Platform plan's per-board suggestions; those keep their text and point here. Done when a freshly flashed card goes from boxed to playing a song with no SSH, no hand edits, and no visit to iOS Settings, on a Pi 4 and on a Pi 5.

## Decided

- **Most of setup happens on a phone, or a browser on a laptop or desktop** (Oct 20): typing passwords and names with a knob is no good. The Juk3box Helper app is one way in, not the only one: any phone or computer that can join a Wi-Fi network and open a page can set up the jukebox.
- **The cabinet screen shows progress and does the hands-on checks**, the ones the owner watches at the cabinet: the test sound, whether the lights look right, the power load test, the knob and keys. The question is asked on the phone or computer ("Do you hear it?"), and the cabinet's knob can answer it too.
- **Lights are optional** (Oct 20). A cabinet may have no LED chains, or only some of the three (buttons, the accent chain of upper panel plus footlights, the level bar). The wizard asks which exist, skips the light steps and the lights' share of the load test for the ones that don't, and every service runs fine with none.
- **Hardware is self-serve** (Oct 14, roadmap item 5). The image carries everything for both boards (both LED drivers, the `[pi4]`/`[pi5]` boot settings, both video profiles). The wizard detects the board, the owner confirms it, and it applies that board's settings by itself. Every step pi5-move.md did by hand ends up in the wizard's code or the image, not just in a doc.
- **Detect first, ask only what can't be read, then check the result.** Each step below says what it reads, what it asks, what it changes, and how it verifies (the power step is the pattern).
- **Per-board suggestions are applied, never forced** (Platform plan, "Per-board suggestions"): a module or feature suggested off for the board starts off, listed on one screen with its reason, each a switch.
- **Sidecar's lessons** (Phase 7b): WMM off for this Wi-Fi chip (CYW43455) in AP mode; phones reset captive-portal pages, so progress is kept on the Pi, never in the page.

## Ways in

1. **The helper app** (helper plan Phase 6): "Set up a new juk3box" joins the setup hotspot with `NEHotspotConfiguration`, shows `/setup/` in a WKWebView, leaves the hotspot after the Wi-Fi step (`removeConfiguration`), rejoins home Wi-Fi and finds the jukebox by Bonjour (`_juk3box._tcp`), then carries on: pairing (Phase 2 flow), MusicKit authorization, the AirPlay route step, the first library sync.
2. **Any phone or computer, by the hotspot**: join `Juk3box Setup XXXX` from the phone's Settings › Wi-Fi (the cabinet screen shows its name and password); the captive portal opens `/setup/`, or type the address the screen shows. (A camera "join this Wi-Fi" QR code was tried until v0.1.2: iOS joined from it but never opened the sign-in sheet, so it's gone.)
3. **Any phone or computer, on the home network**: a jukebox with Ethernet plugged in (or Wi-Fi already set) never needs the hotspot. The screen shows `http://<name>.local:8080/setup` and a QR code for it. This is also how we build and test the wizard on this cabinet before setup mode exists.

## Setup mode (the Pi side of Phase 6)

- **When:** no owner password yet (`/api/session` `setup_needed`) or setup not finished (`/var/lib/juk3-box/setup.json`), and no known network reachable; or the owner asks for it from the Reset-hold menu (open question 1). Then: NetworkManager AP mode, `Juk3box Setup XXXX` (last 4 of the board serial; the name a person reads, so spelled as the product), WMM off, a captive portal (DNS answers everything with the Pi; port 80 sent to the setup pages).
- **jb-netwatch stands down in setup mode.** Today it reconnects, turns the Wi-Fi off and on, and as a last resort reboots when offline (`services/jb_netwatch/policy.py`); in setup mode offline is the point. It reads the setup state and does nothing until setup mode ends.
- **The Wi-Fi scan happens before the AP starts** (the chip can't scan well while it's an access point); the list is kept and refreshed on request by dropping the AP for a few seconds.
- **Leaving it:** after the Wi-Fi step the Pi joins the home network and advertises normally (Bonjour for the app and the admin). If joining fails, the hotspot comes back and the page shows why (wrong password, not found).
- **Where the pages live:** jb-admin, under `/setup/` (it already owns the first owner password, `/api/setup`, and the admin's UI kit), reachable only while setup isn't finished, plus from the admin's Setup page afterwards (Re-running). Open question 9.

## The steps, in order

Answers and progress are kept in `/var/lib/juk3-box/setup.json` as each step completes (written with `shared/files.py`), so a page reload, a phone that drops the hotspot, or the reboot in step 10 resumes where it was. Steps 6–10 only gather changes; the ones that need a reboot are applied together in step 10, so the owner sits through one restart, not four.

| # | Step | Boards | Runs on |
|---|---|---|---|
| 1 | Owner password | all | phone/computer |
| 2 | Name | all | phone/computer |
| 3 | Wi-Fi | all (skipped on Ethernet) | phone/computer, cabinet shows status |
| 4 | Time and region | all | phone/computer |
| 5 | Board | all | phone/computer |
| 6 | Power | Pi 5 asks; Pi 4 checks only | phone/computer |
| 7 | Clock battery | Pi 5 | phone/computer |
| 8 | Cooling | Pi 5 (Pi 4: temperature only) | phone/computer, cabinet |
| 9 | Lights: which ones | all | phone/computer |
| 10 | Apply and restart | all | both |
| 11 | Lights look right | chains that exist | cabinet, answered anywhere |
| 12 | Power load test | all (lights part only for chains that exist) | cabinet, answered anywhere |
| 13 | Sound | all | cabinet, answered anywhere |
| 14 | Screen | all | cabinet |
| 15 | Knob and keys | all | cabinet |
| 16 | Sources | all | phone, cabinet |
| 16a | Last.fm key | all | phone/computer |
| 17 | Whole-home audio | all (Pi 4: cautioned) | phone/computer |
| 18 | Suggestions | boards with any | phone/computer |
| 19 | Finish | all | both |

**1. Owner password.**
Detects: `/api/session` `setup_needed`. Asks: the admin's password (twice). Changes: `/api/setup` (exists: `auth.set_password`). Verifies: signs in. First, so nobody who joins the hotspot after the owner can take the jukebox (open question 5 for the hotspot's own password).

**2. Name.**
Detects: the current name (helper settings `name`, default `Juk3box`). Asks: the jukebox's name. Changes: one name everywhere a phone sees it: the helper app's Bonjour name (helper `settings` command, `name`; exists and re-announces, `services/jb_source_helper/main.py` `renamed`), the AirPlay receiver's name (exists: jb-source-helper writes `/var/lib/juk3-box/helper/airplay.env`, which `jb-airplay.service` passes to shairport-sync as `-a`), the Bluetooth adapter's alias (`org.bluez.Adapter1.Alias`; to build: set by hand to `Juk3box` on this cabinet, Oct 20), and the cabinet screen's title. The hostname is fixed (Answered, 2). Verifies: reads each back.

**3. Wi-Fi.**
Detects: Ethernet with a route out (then the step offers "Skip, I'm on a cable"); networks from the pre-AP scan with signal. Asks: the network and its password (or "type a hidden network"). Changes: a NetworkManager connection; the Wi-Fi country comes from step 4 but is asked here if 5 GHz networks need it first. Verifies: the Pi joins, gets an address, and the router answers a ping (jb-netwatch's checks). Then the page says where to go next (`http://<name>.local:8080/setup`, or the app finds it itself), the hotspot closes, and the cabinet shows the same address with a QR code for anyone whose phone didn't follow. Failure: back to the hotspot, with the reason.

**4. Time and region.**
Detects: the browser's time zone (`Intl.DateTimeFormat().resolvedOptions().timeZone`) and language, sent with the page; NTP sync (`timedatectl`). Asks: confirm the time zone and the country (the Wi-Fi regulatory domain). Changes: `timedatectl set-timezone`, the Wi-Fi country (`raspi-config nonint do_wifi_country` or NetworkManager's). Verifies: the clock is synced.

**5. Board.**
Detects: `shared/hardware.board()`: model, family (`pi4`, `pi5`, `other`), LED driver, video codec, hardware decode; the board serial (`/proc/device-tree/serial-number`) for Re-running. Asks: confirm ("This is a Raspberry Pi 5, 4 GB"). `other`: says JUKE.BOX supports the Pi 4 and Pi 5, and carries on without the board-specific steps. Changes: nothing on its own; the services already read `board()` (jb-lights picks `pio_chains` or `pwm_chains` by `led_driver`; the admin's `/api/board`). The image's `config.txt` already has both `[pi4]` and `[pi5]` sections (pi5-move.md, "Fence the Pi-4-only boot settings"). Verifies: after step 10, `board()` still says the same.

**6. Power.** (pi5-move.md work item 3, "How the Pi 5 is powered".)
- **Pi 5.** `PSU_MAX_CURRENT=5000` is right for the 5 V GPIO pins (2 and 4) and wrong for a 3 A USB-C charger: the firmware would then allow 1.6 A on the USB ports and skip its weak-supply warning, so a heavy load browns the board out instead of being refused. Without it on the GPIO feed, the USB ports are held to 600 mA and the boot warns about the supply.
  Detects: `/proc/device-tree/chosen/power/usbpd_power_data_objects` (the USB-PD offers a USB-C charger made; all zeros on this cabinet, GPIO-fed, Oct 20), `chosen/power/max_current` (what the firmware is using: `0x1388` = 5000 here), the EEPROM's current setting (`rpi-eeprom-config`).
  Asks: only when the offers are all zeros (GPIO pins, or a USB-C charger without PD): "Is the Pi powered through the cabinet's power supply (the GPIO pins), or a USB-C charger?"
  Changes: PD offers present: leave the setting unset, clearing it if set (the firmware knows the charger's real limit). GPIO: set 5000. USB-C without PD: leave it unset and say the charger is weak. `usb_max_current_enable=1` stays in `[pi5]` (already there). Applied in step 10.
  Verifies: after the restart, `max_current` reads what was chosen, `vcgencmd get_throttled` is `0x0`, and the load test (step 12).
- **Pi 4.** No PD and no such setting: nothing to ask. Verifies with `get_throttled` and the load test.

**7. Clock battery.** (Pi 5; pi5-move.md, "Clock battery".)
Why: with no battery the clock restarts at the last shutdown's time, and the first NTP sync jumps it forward; Mosquitto times keepalives on the wall clock and drops every client, and nearly every jb-* service exits and restarts about 30 s into boot. The Pi 4 does it too and has no fix but the robustness work (services that reconnect to the bus instead of exiting; build order 7).
Detects: the Pi 5's RTC (`/dev/rtc0`); whether it kept time across the last power-off (the RTC's time at boot against NTP's, once synced). Asks: "Is a clock battery fitted? The official Raspberry Pi RTC Battery (rechargeable), another battery, or none." Changes: the official one: `dtparam=rtc_bbat_vchg=3000000` in `[pi5]` so it charges (off by default); another battery or none: nothing (never charge a battery that isn't rechargeable). Applied in step 10. Verifies: on the next boot the clock was right before NTP (no jump in the journal).

**8. Cooling.** (Pi 5.)
Detects: the fan on the FAN connector (`/sys/devices/platform/cooling_fan/hwmon/*/fan1_input`, `/sys/class/thermal/cooling_device0`, type `pwm-fan`). Asks: nothing when found. Verifies: spins it, as on Oct 20: `cur_state` 2 then 4 (1,430 and 2,630 rpm with the official Active Cooler), back to 0 for the firmware (off below about 50 °C). Not found: "A Pi 5 in a closed cabinet needs a cooler (the official Active Cooler)", and the load test watches the temperature. Pi 4: no fan header; the temperature is shown during the load test.

**9. Lights: which ones.** (Decided Oct 20: lights are optional.)
WS281x strips can't be read back, so the wizard can't tell an empty pin from a lit strip by itself: it asks, then the owner confirms by eye in step 11.
Asks: which chains the cabinet has, each a switch, with a picture: the buttons (23 RGBW, GPIO12), the upper panel and footlights (the accent chain, 349, GPIO13), the level bar (180, SPI on GPIO10); or "No lights".
Changes: `/etc/juk3-box/lights.json` (`{"buttons": true, "accent": true, "levelbar": true}`; new). On a Pi 5, the `ws2812-pio` overlay lines in `[pi5]` only for chains that exist (applied in step 10); the level bar's SPI (`dtparam=spi=on`) stays, it's harmless. No lights: jb-lights is turned off (the Modules page's switch), and jb-analyzer, whose main use is the light show, is offered off in step 18 (open question 4).
Code to change: jb-lights today runs all three (the buttons in the main thread, the level bar and accent in supervised threads, `services/jb_lights/main.py`), and on a Pi 5 it idles entirely if either PIO device is missing (`pio_chains.available()` wants both). It must run each chain only if `lights.json` says so and its device is there, and stay up with none. Things that use the lights must not mind their absence: the pick doctor's keypad flash, the level bar's volume fill. The chain lengths and the buttons' inverted signal are this cabinet's (Rev E board); other cabinets are open question 3.

**10. Apply and restart.**
Shows what will change and why (the boot settings from steps 6, 7 and 9, the EEPROM setting from step 6) and restarts once. Changes: `config.txt` edits with a dated backup (`config.txt.bak-YYYYMMDD`, as pi5-move.md did), `rpi-eeprom-config --apply` for `PSU_MAX_CURRENT` (needs root: a narrow polkit rule or a small root helper like `deploy/jb-modctl`, never a broad sudo). The cabinet shows "Restarting to apply your settings"; the phone's page waits and reconnects by itself (it polls the setup state; the progress is on the Pi). Nothing to apply: no restart.
Verifies after the restart: `systemctl --failed` empty, `board()` as confirmed, `max_current` as chosen, `/dev/leds-*` present for each chain that exists (Pi 5), `get_throttled` `0x0`.

**11. Lights look right.** (Chains that exist.)
The cabinet lights each chain in turn, white at low brightness then a colour sweep, and the page asks "Are the buttons lit, all 23, in order?" (and the same for the upper panel, footlights, level bar). Answered on the phone or with the knob. No: tips by chain (the buttons' inverted signal on a Pi 5 is the first suspect, pi5-move.md work item 1; a strip joint, like the level bar's 19→20; swapped lines, like Oct 20's level bar and upper panel). A chain marked present that the owner doesn't see can be switched off here.

**12. Power load test.**
The Oct 20 test, run by the wizard: every chain that exists at its software cap for 30 s (accent at `accent.MAX_AMPS` 6 A, the level bar at `matrix.MAX_AMPS` 4 A through `WS2812SPI`'s limiter, the buttons white), estimated at `AMPS_PER_CHANNEL_FULL` 0.020 A per channel, with jb-lights paused for the test and restarted after. Each second: EXT5V (`vcgencmd pmic_read_adc EXT5V_V`, Pi 5 only), `get_throttled`, the temperature, the fan's rpm. No lights: a 30 s CPU load instead (the kiosk plus a decoder), so the supply is still checked.
Passes: EXT5V stays at or above about 5.0 V and `get_throttled` stays `0x0` (Oct 20 on this cabinet: 5.14 V minimum, `0x0`, 53.8 °C). Fails: "Turn the supply up to 5.2–5.25 V at the board (the trimmer on the LRS supply, clockwise), or use shorter, thicker wires to pins 2 and 4", then test again. Pi 4: only `get_throttled`'s undervoltage bits (no EXT5V reading).

**13. Sound.**
Detects: the DAC (from its HAT EEPROM, `aplay -l`; jb-audio's `pw.find_dac`); PipeWire realtime (`ps -eLo cls,rtprio,comm | grep data-loop` shows `FF`: `admin` in the `pipewire` group, the image's job) and `10-jukebox-clock.conf` in place. These are checked, not asked: a failure is the image's bug, shown plainly. No DAC: the Pi's own outputs (HDMI), asked. Asks: plays a test sound on the cabinet at 5% ("Do you hear it?"; the Oct 20 test volume), then the owner sets a comfortable level with the knob. Changes: the starting volume (jb-audio remembers it), a maximum (open in admin Settings).

**14. Screen.**
The cabinet's Settings > Displays steps (jb-display: `/var/lib/juk3-box/display.json`): rotation (keep it or it goes back in 15 s), the picture area with the edge guide so the contents fit the cabinet's glass, screen sleep. Done on the cabinet with the knob, or from the phone (the admin's Displays page); the cabinet shows the guide either way.

**15. Your controls.**
The keypad board (installed or updated first, if it's a Pico), the layout, the knob and other controls, teaching each button, extra buttons, a try. Planned Oct 21: `Controls Setup — Plan.md`.

**16. Sources.** At least one, or "Not now".
- **The helper app.** Came in through the app: the app pairs now (the code on the cabinet; helper `allow_pairing` is on by itself while no phone is paired, `Helper.settings`), then MusicKit authorization, the AirPlay route step (the app's route warning), the first library sync. Didn't: a QR code to the app and the same steps when it opens.
- **Bluetooth.** "Add a phone" on the cabinet (Settings > Sources > Bluetooth; `jb-source-phone/cmd` `pair_start`), then the phone's Bluetooth settings; the library syncs (911 songs on Oct 20).
- **Drives.** "Plug in a USB drive with music"; its songs join the board (jb-drive, jb-source-local). Nothing to set.
Verifies: the source's songs are on the board; one pick plays (the pick doctor watches it).

**16a. Last.fm key.** (Optional; added Oct 20.) Each owner brings their own free key: none ships on the card or in an image.
Detects: a key in `/etc/juk3-box/jb-library.json`. Asks: the key, under a numbered how-to (Last.fm's API page, what to type in its form, copy the API key and not the shared secret). Changes: `jb-setupctl lastfm KEY` writes it to jb-library (`lastfm_api_key`) and jb-analyzer (`lastfm_key`) and restarts them; Weather Mix gets it as its `lastfm-key` secret when installed. Verifies: asks Last.fm about one artist first (a key it turns down is refused; offline, it's saved unchecked). Skipping says what goes without: the board's Top 100, genre-picked light shows, Weather Mix's moods. Added or changed later on the admin's Setup page (`PUT`/`DELETE /api/lastfm`); seal.sh clears it.

**17. Whole-home audio.** (Optional.)
Detects: AirPlay speakers on the network (`jukebox/jb-audio/airplay` `receivers`). Asks: which zone plays along, or none. Changes: whole-home on, the zone, the starting delay (1425 ms on this cabinet) to tune by ear with music (`airplay_match`, the house following the cabinet's knob). Pi 4: shows `FEATURES["pi4"]["whole_home"]`'s caution and cost (a warning, not suggested off).

**18. Suggestions.** (Platform plan, "Per-board suggestions".)
One screen: each module whose `module.json` `boards[<family>]` says `"suggested": false` (`hardware.module_suggestion`; jb-analyzer on a Pi 4) and each feature in `hardware.FEATURES[<family>]` without `warn` (`hardware.feature_suggestion`; background video on a Pi 4), with its why and measured cost, each a switch starting off: "Your Pi 4 runs better without these; you can turn any on later." No lights adds jb-analyzer (open question 4). Nothing listed: the step is skipped. The Pi 5's entries come from measuring there (pi5-move.md "On the Pi 5", 5–6; the background video's cost was being measured on Oct 20).
Changes: the modules' on/off (as the Modules page does), the feature settings (the background falls back to a still picture). The admin keeps labelling them afterwards (built Oct 20).

**19. Finish.**
A summary of every answer, with a link to change each (Re-running). Pick a song on the cabinet. Marks setup done in `setup.json` (with the board serial and the answers); setup mode won't come back by itself unless no known network is reachable and the owner asks for it (open question 1). jb-netwatch resumes. Points to the admin (and the app's Admin tab, helper Phase 7).

## What runs where

| Phone or computer (the setup pages) | Cabinet screen |
|---|---|
| Every question, every name and password | The hotspot's name and password, and a small code for the Helper app; then the address and its QR code |
| Choosing networks, zones, sources, switches | "Step 6 of 19: Power" and a short line of what's happening |
| Answers to the cabinet checks (yes/no, retry) | The checks themselves: each light chain, the load test's live voltage and temperature, the test sound, the edge guide, each knob turn and key press |
| The summary | "Restarting to apply your settings"; "Setup finished" |

The cabinet's part is a jb-ui screen (`setup`, an engine screen like `sources-*`) fed by a retained bus topic, `jukebox/jb-admin/setup` (`step`, `of`, `title`, `prompt`, `check` with live values; to add to contracts/bus.md). The knob can answer its yes/no prompts (`setup` cmd topic), so the owner standing at the cabinet doesn't need the phone in hand for those.

## Re-running and changing an answer

- **The admin gets a Setup page**: every step's answer, each with "Change" (re-runs that step, its checks, and step 10 if it needs a restart). The full wizard can be run again from there.
- **Every step checks reality, not just the stored answer.** The Pi 5's `PSU_MAX_CURRENT` lives in the board's EEPROM, not on the card: it stays with the board when the card moves. So the power step reads and sets or clears it whichever way it's answered, and never assumes.
- **A new board under the same card** (the serial differs from `setup.json`'s): the cabinet says "New board found: check its power and lights" and the admin's Setup page opens the hardware steps (5–12). The old EEPROM setting stays on the old board, so it'll want the same check if it's used again.
- **Lights added or removed later**: step 9, then 10–12.
- **Moving from the GPIO feed to a USB-C charger** (or back): step 6, then 10 and 12.
- **Wi-Fi changed or the jukebox moved house**: no known network, so setup mode starts with the hotspot and only the Wi-Fi step (the rest is done).
- **Starting over** (selling the cabinet): a Reset-hold menu item or the admin; it clears the owner password, phones and setup, keeps the songs on drives, and opens setup mode (open question 8).

## Answered (Oct 20, the owner's call: the recommendations)

1. **Setup mode on purpose:** "Set up Wi-Fi" joins Reboot / Shut down in the Reset-hold menu; no new gesture.
2. **The hostname:** follows the cabinet's one name (decided Oct 21, 2026): "Garage Jukebox" is `garage-jukebox.local`. Setup's Name step checks it's free and Finish moves it (so the setup page isn't lost partway); after setup, Admin › Setup › Name renames it, warning that bookmarks change and the admin moves. The app finds the jukebox over Bonjour each time, so it isn't affected. A new image starts as `juk3box`; the first cabinet moved from `juk3-box` to `juk3box.local`.
3. **Other cabinets' lights:** fixed to Juk3box's own board for now (lengths 23, 349, 180, the GPIOs, the buttons' inversion), kept in one config file (`lights.json`) so making them configurable later is a small change. The step only asks which chains exist.
4. **No lights:** jb-analyzer is suggested off (its main use is the light show), on the suggestions screen like any other.
5. **The hotspot:** a password, shown on the cabinet screen and in its QR code, so only someone at the cabinet can join.
6. **The clock battery:** only "the official Raspberry Pi RTC Battery: yes or no". Charging is turned on for that one only; anything else gets nothing (never charge a battery that isn't rechargeable).
7. **Time zone and country:** from the browser, confirmed with one tap; asked only when the browser gives nothing.
8. **Starting over** keeps the music (the phones' libraries, plays, the media library) and paired phones; it resets the network, the name and the hardware answers, and asks the owner password again.
9. **The setup pages live in jb-admin** (`/setup/`), which already has the first password, the board and the per-board suggestions.
10. **Skippable for later:** sources, whole-home audio and the screen tweaks. Settings shows a quiet "Finish setup" row until they're done; no nagging on Home.

## The look (decided Oct 20)

The owner approved the mockup (claude.ai artifact "Juk3box Setup Wizard", https://claude.ai/artifact/9bB6ZeAMAtUQBZD8WuxvKr: phone flow, laptop browser, cabinet screen). The Raspberry Pi wizard's simplicity in Juk3box's branding:
- One question per screen: a big heading, one or two sentences, big tap targets, Back and Next always in the same place.
- The splash purple `#7a3db0` as the ground, the logo on every screen, the logo's EQ bars moving on the welcome, finish and cabinet screens (bar colours as in the splash: `#22e7d4 #009da4 #0068a6 #ff2198 #ff63d5 #ff9959`). Questions sit on a white card (radius 28), choices are bordered cards that fill `#f5effb` with a `#7a3db0` border when picked, the main button is `#7a3db0` on white.
- Righteous for headings, Nunito for text (both already in the kiosk's fonts, so nothing loads from the internet).
- A phone shows "Step 3 of 7" with dots; a computer shows the step list down the left.
- Plain words: "How is it powered?", "Which lights does it have?", "No lights is fine too".
- The cabinet during setup: the logo and bars, how to join (setup Wi-Fi and its password from Settings › Wi-Fi, the Helper app's code, the address), the progress list, and what to look at right now.

## Naming (decided Oct 20)

The product is **Juk3box** wherever a person reads it: the admin ("Juk3box Admin"), the cabinet, the app ("Juk3box Helper"), the AirPlay speaker and Bluetooth name by default, docs. Not JUKE.BOX, JUK3.BOX, JUK3BOX or juk3-box. Identifiers a person doesn't read stay as they are: `/opt/juk3-box`, `jb-*` services, `jukebox/...` topics, the app's bundle id and `_juk3box._tcp`, the keypad's USB name ("Jukebox JB Keypad": the firmware and udev match it).

## Build order

1. **The step framework in jb-admin**, on the home network (way in 3, no hotspot yet): `/setup/` pages, `setup.json` with resume across reloads and a restart, the admin's Setup page, and the cabinet's `setup` screen and `jukebox/jb-admin/setup` topic.
2. **The hardware steps** on this cabinet (5–12): board, power (with the EEPROM write behind a narrow root helper), clock battery, cooling, lights (with jb-lights per chain and `lights.json`), the one restart, the lights check, the load test. Test with the lights unplugged too.
3. **Sound, screen, knob and keys, sources, whole-home, suggestions, finish** (13–19). The suggestions screen needs the Pi 5's measurements.
4. **Name everywhere** (step 2's Bluetooth alias; the AirPlay name already follows).
5. **Setup mode**: the hotspot (WMM off), the captive portal, the Wi-Fi scan and hand-off, jb-netwatch standing down, the cabinet's join screen (name, password, the Helper app's code) and the address QR code, the Reset-hold entry.
6. **The helper app's side** (helper plan Phase 6): `NEHotspotConfiguration`, the WKWebView, `removeConfiguration`, Bonjour, then pairing, MusicKit, the AirPlay route, the first sync.
7. **Robustness the wizard leans on**: services that reconnect to the bus instead of exiting (the clock jump on boards without a battery).
8. **A fresh card on both boards**, boxed to playing with no SSH; then the image or installer (roadmap item 8).

## Where it stands (Oct 20)

Built, in that order: 1–6 above, and the installer half of 8.

- **1–4** run end to end on the cabinet (the owner finished it Oct 20). The admin's Setup page reads `/api/wizard-status`, which doesn't bring up the cabinet's setup screen. Renaming sets the Bluetooth name too.
- **5, setup mode**, in jb-netwatch (`services/jb_netwatch/setup_mode.py`), talking to jb-admin and jb-core through request files (`shared/setup_net.py`, `/var/lib/juk3-box/setupnet`, admin's own). Starts by itself only on a jukebox that never finished setup and is offline (30 s with no Wi-Fi ever set, 3 min with one); otherwise from the power menu's Set up Wi-Fi, and then gives up after 15 minutes unused or on the cabinet's Cancel. The hotspot is an NM shared AP (`Juk3box Setup XXXX`, a password kept per board), every name answered with 10.42.0.1 (`dnsmasq-shared.d`, only while it's on) and port 80 redirected to the setup page by a small server inside jb-netwatch (no nft on the image). The cabinet says to join from Settings › Wi-Fi, with the name and password, and shows a smaller `JUK3BOX:S:<ssid>;P:<password>;;` code for the Helper app (WIFI:-style fields under its own prefix, so a camera doesn't half-join from it; v0.1.7). Not done: WMM off (NetworkManager has no setting for it; watch for the Sidecar problem).
- **6, the app**: "Set up a new jukebox" on Home (juk3box-helper `Features/Setup`): scans the cabinet's JUK3BOX: code (or an older card's WIFI: one), notes the phone's network (`?ssid=` on the page), joins the hotspot, shows the page; the page tells the app when it starts joining (`webkit.messageHandlers.juk3box`), and the app lets go of the hotspot and finds the jukebox at its new address. Uses the Hotspot Configuration and Access Wi-Fi Information capabilities, both on for the App ID (checked Oct 22 in the app's provisioning profile); without them it would say to join from Settings.
- **8, the card**: `deploy/make-card.sh` (from the Mac) runs `deploy/install/install.sh` on a fresh Raspberry Pi OS Lite and, after a check, `seal.sh`, which forgets the owner, Wi-Fi, phones, songs, the Last.fm key and setup so the next start opens setup mode. No Last.fm key is copied over (step 16a asks the owner).

7 done Oct 22: `shared/bus.py` reconnects by itself, and mosquitto clears its module socket before each start (a restart used to fail until the next boot). Still to do: the knob step's key presses make real picks; the image for copying cards (machine id, SSH host keys, cloud-init).

**The fresh-card test** (the owner's plan, Oct 20; the cabinet's own card is kept as it is):
1. Raspberry Pi Imager: Raspberry Pi OS Lite (64-bit); hostname `juk3box`, user `admin` with the cabinet's password, the home Wi-Fi (only for the install), SSH with `~/.ssh/sidecar_ed25519.pub`.
2. Put it in the cabinet, start it, then `deploy/make-card.sh` (installs and restarts), check the screen, sound and lights, then `deploy/make-card.sh juk3box.local --seal` (powers off).
3. Start it as a new owner would: setup mode, the phone, the whole wizard.
