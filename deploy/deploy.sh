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
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ssh_pi() { ssh -i "$PI_KEY" "$PI_USER@$PI_HOST" "$@"; }

# Sudo on the Pi requires a password (no NOPASSWD configured, deliberately —
# don't add it). Prompt once per deploy run and reuse it for the few remote
# steps that need root.
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

UNIT_NAME="$(echo "$SERVICE_NAME" | tr '_' '-')"
RENDERED_UNIT="$(mktemp)"
sed "s/{{SERVICE_NAME}}/${SERVICE_NAME}/g" "$REPO_DIR/deploy/systemd/jb-service.service.template" > "$RENDERED_UNIT"
scp -i "$PI_KEY" "$RENDERED_UNIT" "$PI_USER@$PI_HOST:/tmp/${UNIT_NAME}.service"
rm -f "$RENDERED_UNIT"
ssh_pi_sudo "mv /tmp/${UNIT_NAME}.service /etc/systemd/system/${UNIT_NAME}.service"

ssh_pi_sudo "systemctl daemon-reload && systemctl enable --now ${UNIT_NAME}.service && systemctl restart ${UNIT_NAME}.service"

echo "Deployed and (re)started ${UNIT_NAME}.service"
