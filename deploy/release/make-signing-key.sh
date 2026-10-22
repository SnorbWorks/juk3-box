#!/bin/sh
# Once, by the owner: makes the key releases are signed with. Jukeboxes
# install an update only if it's signed with it (deploy/jb-update).
#   - the public half: deploy/update/juk3box-update.pub (commit it)
#   - the private half: the Mac's Keychain (juk3box-update-signing, base64)
#     and GitHub's Actions secret UPDATE_SIGNING_KEY, which the release build uses.
# The Mac's openssl (LibreSSL) can't make Ed25519 keys, so the cabinet's does;
# the key goes over SSH and is never written to a file.
set -eu
cd "$(dirname "$0")/../.."
PUB=deploy/update/juk3box-update.pub
PI=${PI_HOST:-juk3box.local}
GH=$(command -v gh || echo "$HOME/bin/gh")
[ ! -f "$PUB" ] || { echo "There's already a key ($PUB). Replacing it means every jukebox needs the new one first." >&2; exit 1; }
KEY=$(ssh -i "$HOME/.ssh/sidecar_ed25519" "admin@$PI" 'openssl genpkey -algorithm ed25519')
mkdir -p "$(dirname "$PUB")"
printf '%s\n' "$KEY" | ssh -i "$HOME/.ssh/sidecar_ed25519" "admin@$PI" 'openssl pkey -pubout' > "$PUB"
security add-generic-password -U -a juk3box -s juk3box-update-signing -w "$(printf '%s\n' "$KEY" | base64)"
printf '%s\n' "$KEY" | "$GH" secret set UPDATE_SIGNING_KEY -R SnorbWorks/juk3-box
echo "Made the update signing key: $PUB, the Keychain, and GitHub's UPDATE_SIGNING_KEY."
