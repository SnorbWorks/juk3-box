#!/bin/sh
# Builds a Juk3box card image: Raspberry Pi OS Lite with the jukebox
# installed and sealed, so the first start of every copy opens setup mode.
# Runs as root on 64-bit ARM Linux (GitHub's arm64 runners, or a Pi):
#
#   sudo sh deploy/image/build-image.sh SOURCE.tar.gz VERSION OUT_DIR
#
# SOURCE.tar.gz is the code (git archive of a release tag). Makes
# OUT_DIR/juk3box-VERSION.img.xz and OUT_DIR/juk3box-VERSION.json (sizes
# and checksums for Raspberry Pi Imager's list). The base image is pinned
# below; move it forward deliberately, not by accident.
set -eu

BASE_URL=${BASE_URL:-https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz}
BASE_SHA256=${BASE_SHA256:-483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843}
GROW_MB=${GROW_MB:-4096}           # room for the packages while installing (shrunk back after)
SLACK_MB=${SLACK_MB:-256}          # left free in the shrunk image; the first start fills the card
JB_USER=admin

SRC=${1:?source tarball}
VERSION=${2:?version}
OUT=${3:?output folder}
WORK=${WORK:-$(pwd)/image-work}

say() { echo "== $*"; }
die() { echo "build-image.sh: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run it as root"
[ "$(uname -m)" = aarch64 ] || die "needs a 64-bit ARM machine (the image's programs run during the build)"
case $VERSION in v[0-9]*) ;; *) die "version like v1.0.0, not $VERSION" ;; esac
for t in losetup parted e2fsck resize2fs xz curl sha256sum; do command -v $t >/dev/null || die "missing $t"; done

mkdir -p "$WORK" "$OUT"
IMG=$WORK/juk3box.img
ROOT=$WORK/root
LOOP=

cleanup() {
  set +e
  for m in boot/firmware dev/pts dev proc sys run; do
    mountpoint -q "$ROOT/$m" && umount -l "$ROOT/$m"
  done
  mountpoint -q "$ROOT" && umount -l "$ROOT"
  [ -n "$LOOP" ] && losetup -d "$LOOP"
}
trap cleanup EXIT

say "Raspberry Pi OS Lite"
BASE=$WORK/base.img.xz
[ -f "$BASE" ] || curl -fsSL -o "$BASE" "$BASE_URL"
echo "$BASE_SHA256  $BASE" | sha256sum -c - >/dev/null || die "the base image's checksum doesn't match"
xz -dc "$BASE" > "$IMG"

say "Room to install"
truncate -s +"${GROW_MB}M" "$IMG"
parted -s "$IMG" resizepart 2 100%
LOOP=$(losetup -fP --show "$IMG")
e2fsck -fy "${LOOP}p2" >/dev/null 2>&1 || [ $? -le 1 ] || die "e2fsck failed"
resize2fs "${LOOP}p2" >/dev/null

say "Mounting"
mkdir -p "$ROOT"
mount "${LOOP}p2" "$ROOT"
mount "${LOOP}p1" "$ROOT/boot/firmware"
for m in dev dev/pts proc sys run; do mount --bind "/$m" "$ROOT/$m"; done
# No services start while packages install (they'd start on the build machine).
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"
chmod 0755 "$ROOT/usr/sbin/policy-rc.d"
mv "$ROOT/etc/resolv.conf" "$WORK/resolv.conf.image"
cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
in_root() { chroot "$ROOT" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 JB_IMAGE_BUILD=1 "$@"; }

say "The $JB_USER account (no password: nobody logs in; the screen logs itself in)"
in_root /usr/lib/userconf-pi/userconf "$JB_USER" ""
in_root passwd -l "$JB_USER" >/dev/null
echo juk3box > "$ROOT/etc/hostname"
sed -i 's/\braspberrypi\b/juk3box/g' "$ROOT/etc/hosts"

say "The code ($VERSION)"
mkdir -p "$ROOT/opt/juk3-box"
tar -xzf "$SRC" -C "$ROOT/opt/juk3-box"
echo "$VERSION" > "$ROOT/opt/juk3-box/VERSION"

say "Installing"
in_root sh /opt/juk3-box/deploy/install/install.sh

say "Wi-Fi on from the first start (setup mode's hotspot; the setup wizard sets the country)"
mkdir -p "$ROOT/var/lib/systemd/rfkill"
for dev in platform-1001100000.mmc platform-fe300000.mmcnr; do echo 0 > "$ROOT/var/lib/systemd/rfkill/$dev:wlan"; done

say "Sealing"
in_root sh /opt/juk3-box/deploy/install/seal.sh --yes --image

rm -f "$ROOT/usr/sbin/policy-rc.d"
rm -f "$ROOT/etc/resolv.conf"
mv "$WORK/resolv.conf.image" "$ROOT/etc/resolv.conf"
cleanup
LOOP=
trap - EXIT

say "Shrinking (the first start grows it to fill the card)"
LOOP=$(losetup -fP --show "$IMG")
e2fsck -fy "${LOOP}p2" >/dev/null 2>&1 || [ $? -le 1 ] || die "e2fsck failed"
resize2fs -M "${LOOP}p2" >/dev/null 2>&1
BLOCKS=$(dumpe2fs -h "${LOOP}p2" 2>/dev/null | sed -n 's/^Block count: *//p')
BSIZE=$(dumpe2fs -h "${LOOP}p2" 2>/dev/null | sed -n 's/^Block size: *//p')
FS_BYTES=$((BLOCKS * BSIZE + SLACK_MB * 1024 * 1024))
resize2fs "${LOOP}p2" "$((FS_BYTES / 1024))K" >/dev/null 2>&1
START=$(parted -ms "$IMG" unit B print | awk -F: '$1 == 2 {sub("B", "", $2); print $2}')
losetup -d "$LOOP"
LOOP=
END=$((START + FS_BYTES - 1))
echo Yes | parted ---pretend-input-tty "$IMG" unit B resizepart 2 "${END}B" >/dev/null 2>&1
truncate -s $((END + 1)) "$IMG"

say "Compressing"
NAME=juk3box-$VERSION
EXTRACT_SIZE=$(stat -c %s "$IMG")
EXTRACT_SHA=$(sha256sum "$IMG" | cut -d' ' -f1)
xz -T0 -6 -c "$IMG" > "$OUT/$NAME.img.xz"
cat > "$OUT/$NAME.json" <<EOF
{"version": "$VERSION", "file": "$NAME.img.xz",
 "extract_size": $EXTRACT_SIZE, "extract_sha256": "$EXTRACT_SHA",
 "image_download_size": $(stat -c %s "$OUT/$NAME.img.xz"),
 "image_download_sha256": "$(sha256sum "$OUT/$NAME.img.xz" | cut -d' ' -f1)",
 "release_date": "$(date -u +%Y-%m-%d)"}
EOF
rm -f "$IMG"
say "Made $OUT/$NAME.img.xz ($(du -h "$OUT/$NAME.img.xz" | cut -f1))"
