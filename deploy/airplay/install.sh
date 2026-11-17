#!/bin/sh
# Installs Juk3box's AirPlay receiver on the Pi on its own (run there with
# sudo, after a deploy has put the repo in /opt/juk3-box). The full installer
# (deploy/install/install.sh) does the same:
#   sudo sh /opt/juk3-box/deploy/airplay/install.sh
set -eu
REPO=/opt/juk3-box
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends shairport-sync
systemctl disable --now shairport-sync 2>/dev/null || true
install -d -m 0755 /usr/local/lib/juk3-box/airplay
install -o root -g root -m 0755 "$REPO"/deploy/airplay/shairport-sync-[0-9]* /usr/local/lib/juk3-box/airplay/shairport-sync
install -o root -g root -m 0755 "$REPO"/deploy/airplay/nqptp-[0-9]* /usr/local/lib/juk3-box/airplay/nqptp
install -o root -g root -m 0755 "$REPO/deploy/airplay/jb-airplay" /usr/local/sbin/
install -o root -g root -m 0644 "$REPO/deploy/systemd/jb-nqptp.service" "$REPO/deploy/systemd/jb-airplay.service" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now jb-nqptp.service
systemctl enable jb-airplay.service
systemctl restart jb-airplay.service
