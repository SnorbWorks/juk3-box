#!/usr/bin/env bash
# Makes a Juk3box out of a freshly flashed card, from this Mac.
#
#   1. Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager. In its
#      settings: hostname juk3box, user admin with the same password as the
#      cabinet (the Keychain's juk3-box-sudo), your Wi-Fi (only for the
#      install: sealing forgets it), SSH on with this Mac's key
#      (~/.ssh/sidecar_ed25519.pub).
#   2. Boot it in the cabinet, then:  deploy/make-card.sh [host]   (default juk3box.local)
#   3. It installs everything and restarts. Check the jukebox, then seal it:
#        deploy/make-card.sh [host] --seal
#      It powers off; the next start is a new jukebox's first: setup mode.
#
# No Last.fm key goes on the card: the setup wizard asks each owner for their own.
set -euo pipefail

PI_HOST="${1:-juk3box.local}"
SEAL=no
[ "${2:-}" = "--seal" ] && SEAL=yes
PI_USER="admin"
PI_KEY="$HOME/.ssh/sidecar_ed25519"
REMOTE_DIR="/opt/juk3-box"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ssh_pi() { ssh -i "$PI_KEY" -o StrictHostKeyChecking=accept-new "$PI_USER@$PI_HOST" "$@"; }
if [ -z "${PI_SUDO_PASS:-}" ]; then
  PI_SUDO_PASS="$(security find-generic-password -a "$PI_USER" -s juk3-box-sudo -w 2>/dev/null || true)"
fi
if [ -z "${PI_SUDO_PASS:-}" ]; then
  read -r -s -p "Pi sudo password (admin@$PI_HOST): " PI_SUDO_PASS
  echo
fi
# The password goes in on stdin, never on a command line.
ssh_pi_sudo() { printf '%s\n' "$PI_SUDO_PASS" | ssh_pi "sudo -S -p '' sh -c '$*'"; }

if [ "$SEAL" = yes ]; then
  ssh_pi_sudo "sh $REMOTE_DIR/deploy/install/seal.sh --yes"
  echo "Sealed and powering off. Next start: setup mode (Juk3box Setup XXXX)."
  exit 0
fi

echo "== Copying the jukebox to $PI_HOST"
ssh_pi_sudo "mkdir -p $REMOTE_DIR && chown $PI_USER:$PI_USER $REMOTE_DIR"
# The working copy, as deploy.sh sends it (with the licensed videos and
# pictures git doesn't have), minus the junk.
rsync -az --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude 'venv' \
  --exclude 'ssh_dummy' --exclude 'temp files' --exclude '.DS_Store' \
  -e "ssh -i $PI_KEY" "$REPO_DIR"/ "$PI_USER@$PI_HOST:$REMOTE_DIR"/

echo "== Installing (a few minutes: packages, then the Python ones)"
ssh_pi_sudo "sh $REMOTE_DIR/deploy/install/install.sh"

echo "== Restarting"
ssh_pi_sudo "systemctl reboot" || true
echo "Give it a minute, check the cabinet, then: deploy/make-card.sh $PI_HOST --seal"
