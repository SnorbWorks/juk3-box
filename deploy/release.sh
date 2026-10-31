#!/bin/sh
# Makes a release:  deploy/release.sh v1.2.3
# Tags the committed code and pushes the tag; GitHub then builds the card
# image and the signed update (.github/workflows/release.yml, about 10 minutes)
# and publishes them on the repo's Releases page. Jukeboxes find it there.
set -eu
cd "$(dirname "$0")/.."
TAG=${1:?usage: deploy/release.sh v1.2.3}
GH=$(command -v gh || echo "$HOME/bin/gh")
echo "$TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' || { echo "A version like v1.2.3, please." >&2; exit 2; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "Commit or put away the changes first." >&2; exit 1; }
[ -f deploy/update/juk3box-update.pub ] || { echo "No signing key yet: run deploy/release/make-signing-key.sh once." >&2; exit 1; }
git fetch -q origin --tags
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "$TAG already exists." >&2; exit 1; }
git push -q origin HEAD
git tag -a "$TAG" -m "Juk3box $TAG"
git push -q origin "$TAG"
echo "Tagged $TAG. GitHub is building it:"
sleep 10
"$GH" run list --workflow release.yml --limit 1
echo "Watch it with: $GH run watch \$($GH run list --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId')"
