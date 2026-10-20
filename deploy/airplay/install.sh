#!/bin/sh
# Installs JUKE.BOX's AirPlay receiver on the Pi (run there with sudo, after
# a deploy has put the repo in /opt/juk3-box):
#   sudo sh /opt/juk3-box/deploy/airplay/install.sh
set -eu
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends shairport-sync
systemctl disable --now shairport-sync 2>/dev/null || true
install -o root -g root -m 0644 /opt/juk3-box/deploy/systemd/jb-airplay.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now jb-airplay.service
