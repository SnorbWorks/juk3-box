# Kiosk display setup

How the Pi boots straight into the jukebox screen:

1. **Autologin** — `getty-autologin.conf` makes tty1 log `admin` in automatically.
   Installed at `/etc/systemd/system/getty@tty1.service.d/autologin.conf`.
2. **Start the compositor** — admin's `~/.profile` ends with:
   ```sh
   # JUKE.BOX kiosk autostart
   if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
     exec sway
   fi
   ```
   `exec` means a crash of sway/Chromium ends the login shell, so getty logs
   back in and the kiosk restarts itself.
3. **Sway** (the Wayland compositor that owns the screen) — `sway-config`,
   installed at `~admin/.config/sway/config`. Hides the cursor, forces the
   browser fullscreen, and runs the launcher.
4. **Browser** — `kiosk-chromium.sh`, run straight from the deployed repo
   (`/opt/juk3-box/deploy/kiosk/`), so `deploy.sh` keeps it current.

## Installing on a fresh Pi

```sh
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo cp /opt/juk3-box/deploy/kiosk/getty-autologin.conf /etc/systemd/system/getty@tty1.service.d/autologin.conf
mkdir -p ~/.config/sway
cp /opt/juk3-box/deploy/kiosk/sway-config ~/.config/sway/config
# then append the ~/.profile snippet above
sudo apt install sway chromium
```

## Reloading after a UI or config change

No live reload — restart the whole kiosk session:

```sh
sudo systemctl restart getty@tty1
```

## Debugging the page

Pass extra Chromium flags through the launcher in the sway config, e.g.
`exec /opt/juk3-box/deploy/kiosk/kiosk-chromium.sh --remote-debugging-port=9222`,
then drive the page over the Chrome DevTools Protocol from the Pi
(`curl localhost:9222/json`). Remove the flag when done.
