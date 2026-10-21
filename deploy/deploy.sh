#!/usr/bin/env bash
# Sync the repo to the Pi and (re)start one service.
# Usage: deploy/deploy.sh <service-name>
set -euo pipefail

PI_HOST="juk3-box.local"
PI_USER="admin"
PI_KEY="$HOME/.ssh/sidecar_ed25519"
REMOTE_DIR="/opt/juk3-box"
# Persistent runtime data (SQLite DBs, etc) lives outside REMOTE_DIR since
# that whole tree gets rsync --delete'd from the repo on every deploy.
DATA_DIR="/var/lib/juk3-box"

SERVICE_NAME="${1:?Usage: deploy.sh <service-name>}"
# The Python package name (jb_ui); the unit name (jb-ui) works too.
SERVICE_NAME="$(echo "$SERVICE_NAME" | tr "-" "_")"
if [ ! -d "$(dirname "${BASH_SOURCE[0]}")/../services/$SERVICE_NAME" ]; then
  echo "No service named $SERVICE_NAME in services/" >&2
  exit 1
fi
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ssh_pi() { ssh -i "$PI_KEY" "$PI_USER@$PI_HOST" "$@"; }

# Sudo on the Pi requires a password (no NOPASSWD configured, deliberately —
# don't add it). Prompt once per deploy run and reuse it for the few remote
# steps that need root. If the owner has stored it in the Mac's Keychain
# (security add-generic-password -a admin -s juk3-box-sudo -w), that's used
# instead, so a deploy can run without anyone typing it.
if [ -z "${PI_SUDO_PASS:-}" ]; then
  PI_SUDO_PASS="$(security find-generic-password -a "$PI_USER" -s juk3-box-sudo -w 2>/dev/null || true)"
fi
if [ -z "${PI_SUDO_PASS:-}" ]; then
  read -r -s -p "Pi sudo password (admin@$PI_HOST): " PI_SUDO_PASS
  echo
fi
ssh_pi_sudo() { ssh_pi "echo '$PI_SUDO_PASS' | sudo -S -p '' sh -c '$*'"; }

ssh_pi_sudo "mkdir -p $REMOTE_DIR && chown $PI_USER:$PI_USER $REMOTE_DIR"
ssh_pi_sudo "mkdir -p $DATA_DIR && chown $PI_USER:$PI_USER $DATA_DIR"

rsync -az --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude 'venv' \
  -e "ssh -i $PI_KEY" \
  "$REPO_DIR"/ "$PI_USER@$PI_HOST:$REMOTE_DIR"/

ssh_pi "cd $REMOTE_DIR && python3 -m venv --system-site-packages venv 2>/dev/null; venv/bin/pip install -q -r deploy/requirements.txt"

# The module manager's privileged step (docs/contracts/installed-modules.md): a
# root-owned copy of jb-modctl, run by systemd when jb-admin drops a request
# in its own private folder. No sudo rule involved.
ssh_pi_sudo "install -o root -g root -m 0755 $REMOTE_DIR/deploy/jb-modctl /usr/local/sbin/jb-modctl && install -o root -g root -m 0644 $REMOTE_DIR/deploy/systemd/jb-modctl.service $REMOTE_DIR/deploy/systemd/jb-modctl.path /etc/systemd/system/ && install -d -o $PI_USER -g $PI_USER -m 0700 $DATA_DIR/modctl $DATA_DIR/modctl/requests && systemctl daemon-reload && systemctl enable --now jb-modctl.path >/dev/null 2>&1"

# The setup wizard's privileged steps (docs/Setup Wizard — Plan.md): the same
# arrangement as jb-modctl, with its own folder.
ssh_pi_sudo "install -o root -g root -m 0755 $REMOTE_DIR/deploy/jb-setupctl /usr/local/sbin/jb-setupctl && install -o root -g root -m 0644 $REMOTE_DIR/deploy/systemd/jb-setupctl.service $REMOTE_DIR/deploy/systemd/jb-setupctl.path /etc/systemd/system/ && install -d -o $PI_USER -g $PI_USER -m 0700 $DATA_DIR/setupctl $DATA_DIR/setupctl/requests && systemctl daemon-reload && systemctl enable --now jb-setupctl.path >/dev/null 2>&1"

# USB drives (jb-source-local): mounted read-only by a root-owned helper that
# udev has systemd start when one is plugged in. The trigger mounts any
# drive that's already there.
ssh_pi_sudo "install -o root -g root -m 0755 $REMOTE_DIR/deploy/jb-drive /usr/local/sbin/jb-drive && install -o root -g root -m 0644 $REMOTE_DIR/deploy/systemd/jb-drive@.service /etc/systemd/system/ && install -o root -g root -m 0644 $REMOTE_DIR/deploy/udev/99-jb-drive.rules /etc/udev/rules.d/ && install -o root -g root -m 0644 $REMOTE_DIR/deploy/polkit/51-juk3-box-drives.rules /etc/polkit-1/rules.d/ && systemctl daemon-reload && udevadm control --reload && udevadm trigger --action=add --subsystem-match=block --property-match=ID_BUS=usb"

UNIT_NAME="$(echo "$SERVICE_NAME" | tr '_' '-')"
RENDERED_UNIT="$(mktemp)"
# A service with its own unit file (e.g. jb-lights, which runs as root) uses
# that; everything else renders the shared template.
if [ -f "$REPO_DIR/deploy/systemd/${UNIT_NAME}.service" ]; then
  cp "$REPO_DIR/deploy/systemd/${UNIT_NAME}.service" "$RENDERED_UNIT"
else
  sed "s/{{SERVICE_NAME}}/${SERVICE_NAME}/g" "$REPO_DIR/deploy/systemd/jb-service.service.template" > "$RENDERED_UNIT"
fi
scp -i "$PI_KEY" "$RENDERED_UNIT" "$PI_USER@$PI_HOST:/tmp/${UNIT_NAME}.service"
rm -f "$RENDERED_UNIT"
ssh_pi_sudo "mv /tmp/${UNIT_NAME}.service /etc/systemd/system/${UNIT_NAME}.service"

ssh_pi_sudo "systemctl daemon-reload && systemctl enable --now ${UNIT_NAME}.service && systemctl restart ${UNIT_NAME}.service"

echo "Deployed and (re)started ${UNIT_NAME}.service"
