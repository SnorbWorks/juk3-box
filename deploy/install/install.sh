#!/bin/sh
# Turns a fresh Raspberry Pi OS Lite (64-bit, trixie) into a Juk3box: the
# packages, the services, the kiosk, the boot settings. Run on the Pi as
# root once the repo is at /opt/juk3-box (deploy/make-card.sh does both):
#   sudo sh /opt/juk3-box/deploy/install/install.sh
# Safe to run again. Then reboot; deploy/install/seal.sh makes the card
# start as never set up (the setup wizard, docs/Setup Wizard — Plan.md).
#
# What it does is the first Pi 5 cabinet's setup, taken stock of on
# Oct 20, 2026 (packages from apt's history, files compared with the repo).
set -eu

REPO=/opt/juk3-box
DATA=/var/lib/juk3-box
JB_USER=${JB_USER:-admin}
HOME_DIR=$(getent passwd "$JB_USER" | cut -d: -f6)
BOOT=/boot/firmware
SERVICES="jb_admin jb_analyzer jb_core jb_display jb_input jb_library jb_source_helper jb_source_local jb_source_phone jb_ui"
OWN_UNITS="jb-audio jb-crawl jb-lights jb-netwatch jb-airplay"

say() { echo "== $*"; }
die() { echo "install.sh: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run it as root (sudo)"
[ -n "$HOME_DIR" ] || die "no user $JB_USER"
[ -f "$REPO/services/jb_core/main.py" ] || die "the repo isn't at $REPO (deploy/make-card.sh copies it)"

say "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q mosquitto mosquitto-clients python3-venv \
  pipewire wireplumber pipewire-audio pipewire-alsa alsa-utils chromium seatd sway
# Without their recommends, as on the first cabinet.
apt-get install -y -q --no-install-recommends ffmpeg fonts-noto-cjk build-essential python3-dev \
  mpv shairport-sync grim
# The AirPlay receiver runs as jb-airplay, not the package's own service.
systemctl disable --now shairport-sync 2>/dev/null || true

say "The code and its Python packages"
chown -R "$JB_USER:$JB_USER" "$REPO"
runuser -u "$JB_USER" -- sh -c "cd $REPO && python3 -m venv --system-site-packages venv && \
  venv/bin/pip install -q -r deploy/requirements.txt -c deploy/install/constraints.txt"

say "Folders"
install -d -o "$JB_USER" -g "$JB_USER" "$DATA"
for d in modctl setupctl setupnet; do
  install -d -o "$JB_USER" -g "$JB_USER" -m 0700 "$DATA/$d" "$DATA/$d/requests"
done

say "Root helpers, services, rules"
install -o root -g root -m 0755 "$REPO/deploy/jb-modctl" "$REPO/deploy/jb-setupctl" "$REPO/deploy/jb-drive" /usr/local/sbin/
for u in jb-modctl.service jb-modctl.path jb-setupctl.service jb-setupctl.path jb-drive@.service; do
  install -o root -g root -m 0644 "$REPO/deploy/systemd/$u" /etc/systemd/system/
done
for u in $OWN_UNITS; do
  install -o root -g root -m 0644 "$REPO/deploy/systemd/$u.service" /etc/systemd/system/
done
for s in $SERVICES; do
  unit=$(echo "$s" | tr _ -)
  sed "s/{{SERVICE_NAME}}/$s/g" "$REPO/deploy/systemd/jb-service.service.template" > "/etc/systemd/system/$unit.service"
  chmod 0644 "/etc/systemd/system/$unit.service"
done
install -o root -g root -m 0644 "$REPO/deploy/udev/99-jb-drive.rules" "$REPO/deploy/udev/99-jb-keypad.rules" /etc/udev/rules.d/
install -d -m 0755 /etc/polkit-1/rules.d
install -o root -g root -m 0644 "$REPO/deploy/polkit/50-juk3-box-power.rules" "$REPO/deploy/polkit/51-juk3-box-drives.rules" /etc/polkit-1/rules.d/
install -D -o root -g root -m 0644 "$REPO/deploy/kiosk/getty-autologin.conf" /etc/systemd/system/getty@tty1.service.d/autologin.conf
install -D -o root -g root -m 0644 "$REPO/deploy/install/journald.conf" /etc/systemd/journald.conf.d/50-juk3-box.conf
install -D -o root -g root -m 0644 "$REPO/deploy/install/nm-wifi.conf" /etc/NetworkManager/conf.d/50-juk3box-wifi.conf

say "Overflow swap"
if [ ! -f /var/swapoverflow ]; then
  fallocate -l 512M /var/swapoverflow
  chmod 600 /var/swapoverflow
  mkswap /var/swapoverflow >/dev/null
fi
install -o root -g root -m 0644 "$REPO/deploy/swap/var-swapoverflow.swap" /etc/systemd/system/

say "The bus (mosquitto, with installed modules' private socket)"
/usr/local/sbin/jb-modctl setup

say "$JB_USER's account: realtime audio, user services without a login, the kiosk"
usermod -aG pipewire "$JB_USER"
loginctl enable-linger "$JB_USER"
as_user_install() { install -D -o "$JB_USER" -g "$JB_USER" -m 0644 "$1" "$HOME_DIR/$2"; }
as_user_install "$REPO/deploy/kiosk/sway-config" .config/sway/config
as_user_install "$REPO/deploy/kiosk/fontconfig-fonts.conf" .config/fontconfig/fonts.conf
as_user_install "$REPO/deploy/pipewire/10-jukebox-clock.conf" .config/pipewire/pipewire.conf.d/10-jukebox-clock.conf
as_user_install "$REPO/deploy/install/wireplumber-override.conf" .config/systemd/user/wireplumber.service.d/override.conf
chown -R "$JB_USER:$JB_USER" "$HOME_DIR/.config"
if ! grep -q "Juk3box kiosk autostart" "$HOME_DIR/.profile" 2>/dev/null; then
  { echo; cat "$REPO/deploy/install/profile-kiosk.sh"; } >> "$HOME_DIR/.profile"
fi

say "Boot settings"
CFG=$BOOT/config.txt
[ -f "$CFG.bak-juk3box-install" ] || cp "$CFG" "$CFG.bak-juk3box-install"
sed -i 's/^dtparam=audio=on$/dtparam=audio=off/' "$CFG"
grep -q '^dtparam=audio=off' "$CFG" || echo "dtparam=audio=off" >> "$CFG"
sed -i '/^dtoverlay=vc4-kms-v3d/{/cma-/!s/$/,cma-256/}' "$CFG"
# Our block at the end, between markers (replaced on a rerun).
if grep -q '^# --- juk3box base: BEGIN' "$CFG"; then
  grep -q '^# --- juk3box base: END' "$CFG" || die "$CFG has our BEGIN line but no END: fix it by hand"
  sed -i '/^# --- juk3box base: BEGIN/,/^# --- juk3box base: END/d' "$CFG"
fi
{ echo "# --- juk3box base: BEGIN (deploy/install/install.sh)"; cat "$REPO/deploy/install/config-txt.block"; \
  echo "# --- juk3box base: END"; } >> "$CFG"
# The memory cgroup (MemoryMax for jb-crawl and installed modules).
grep -q 'cgroup_enable=memory' "$BOOT/cmdline.txt" || sed -i '1s/$/ cgroup_enable=memory/' "$BOOT/cmdline.txt"

say "Clock from the internet"
timedatectl set-ntp true || true

say "Turning it all on"
systemctl daemon-reload
udevadm control --reload
systemctl enable mosquitto jb-modctl.path jb-setupctl.path var-swapoverflow.swap >/dev/null
for s in $SERVICES; do systemctl enable "$(echo "$s" | tr _ -)" >/dev/null; done
for u in $OWN_UNITS; do systemctl enable "$u" >/dev/null; done

say "Done. Reboot to start the jukebox: sudo reboot"
