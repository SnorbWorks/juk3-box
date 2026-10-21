#!/bin/sh
# Makes an installed card start as a jukebox nobody has set up: no owner
# password, no Wi-Fi, no phones, no songs, no Last.fm key, no setup progress. On the next
# start it opens setup mode (its own Wi-Fi, docs/Setup Wizard — Plan.md).
# Run on the Pi as root, then it powers off:
#   sudo sh /opt/juk3-box/deploy/install/seal.sh --yes [--for-owner] [--no-poweroff]
#
# --for-owner also removes the SSH keys that let a developer in. Without it
# (a test card) they stay, so its logs can still be read during setup.
# Machine id and SSH host keys are kept: this seals one card, it doesn't
# make an image to copy (that's roadmap item 8).
set -eu

DATA=/var/lib/juk3-box
JB_USER=${JB_USER:-admin}
HOME_DIR=$(getent passwd "$JB_USER" | cut -d: -f6)
BOOT=/boot/firmware
YES=no FOR_OWNER=no POWEROFF=yes
for a in "$@"; do
  case $a in
    --yes) YES=yes ;;
    --for-owner) FOR_OWNER=yes ;;
    --no-poweroff) POWEROFF=no ;;
    *) echo "seal.sh: unknown option $a" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "seal.sh: run it as root (sudo)" >&2; exit 1; }
[ "$YES" = yes ] || { echo "seal.sh: this forgets the owner, the Wi-Fi, phones and songs on this card. Add --yes." >&2; exit 2; }

say() { echo "== $*"; }

say "Stopping the jukebox"
systemctl stop 'jb-*' 'jbmod-*' 2>/dev/null || true

say "Juk3box's own state (keeping the request folders, empty)"
for p in "$DATA"/* "$DATA"/.[!.]*; do
  [ -e "$p" ] || continue
  case $(basename "$p") in
    modctl|setupctl|setupnet) find "$p" -mindepth 1 -type f -delete ;;
    *) rm -rf "$p" ;;
  esac
done
rm -rf /var/lib/private/juk3-box-modules

say "Networks: Wi-Fi and its password, and the Imager's copy of them"
rm -f /etc/NetworkManager/system-connections/*
rm -f /etc/netplan/*.yaml
rm -f "$BOOT/user-data" "$BOOT/network-config" "$BOOT/meta-data"
[ -d /etc/cloud ] && touch /etc/cloud/cloud-init.disabled

say "The owner's Last.fm key (setup asks the next owner for theirs)"
/usr/local/sbin/jb-setupctl lastfm clear >/dev/null

say "Bluetooth pairings"
for adapter in /var/lib/bluetooth/*; do
  [ -d "$adapter" ] || continue
  find "$adapter" -mindepth 1 -maxdepth 1 -type d -delete 2>/dev/null || \
    find "$adapter" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
  rm -rf "$adapter/cache"
done

say "Boot settings the setup wizard makes (it makes them again)"
# (jb-setupctl's block: deploy/jb-setupctl BEGIN and END. Only if both are there,
# so a missing end can never take the rest of the file with it.)
if grep -q '^# --- juk3box setup:' "$BOOT/config.txt" && grep -q '^# --- end juk3box setup ---' "$BOOT/config.txt"; then
  sed -i '/^# --- juk3box setup:/,/^# --- end juk3box setup ---/d' "$BOOT/config.txt"
fi
rm -f "$BOOT"/config.txt.bak-* "$BOOT"/cmdline.txt.bak-*

say "Logs, caches, history"
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
rm -rf "$HOME_DIR/.cache" "$HOME_DIR"/.config/chromium* "$HOME_DIR/.local/state/wireplumber" \
  "$HOME_DIR/.config/pulse/cookie" "$HOME_DIR/.bash_history" /root/.bash_history
apt-get clean

if [ "$FOR_OWNER" = yes ]; then
  say "Developer access"
  rm -f "$HOME_DIR/.ssh/authorized_keys"
fi

sync
if [ "$POWEROFF" = yes ]; then
  say "Sealed. Powering off: the next start is a new jukebox's first."
  systemctl poweroff
else
  say "Sealed. The next start is a new jukebox's first."
fi
