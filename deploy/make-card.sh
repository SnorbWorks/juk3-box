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
# Last.fm keys (genres and similar songs) aren't in the repo: put
# jb-library.json and jb-analyzer.json in ~/.juk3box/secrets/ and they're
# copied over (root:admin 0640 in /etc/juk3-box).
set -euo pipefail

PI_HOST="${1:-juk3box.local}"
SEAL=no
[ "${2:-}" = "--seal" ] && SEAL=yes
PI_USER="admin"
PI_KEY="$HOME/.ssh/sidecar_ed25519"
REMOTE_DIR="/opt/juk3-box"
SECRETS="$HOME/.juk3box/secrets"
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

if [ -d "$SECRETS" ]; then
  echo "== Last.fm keys"
  ssh_pi "mkdir -p -m 0700 /tmp/jb-secrets"
  for f in jb-library.json jb-analyzer.json; do
    [ -f "$SECRETS/$f" ] && scp -q -i "$PI_KEY" "$SECRETS/$f" "$PI_USER@$PI_HOST:/tmp/jb-secrets/$f"
  done
  ssh_pi_sudo "install -d -m 0755 /etc/juk3-box && for f in /tmp/jb-secrets/*.json; do [ -f \$f ] && install -o root -g $PI_USER -m 0640 \$f /etc/juk3-box/; done; rm -rf /tmp/jb-secrets"
fi

echo "== Installing (a few minutes: packages, then the Python ones)"
ssh_pi_sudo "sh $REMOTE_DIR/deploy/install/install.sh"

echo "== Restarting"
ssh_pi_sudo "systemctl reboot" || true
echo "Give it a minute, check the cabinet, then: deploy/make-card.sh $PI_HOST --seal"
