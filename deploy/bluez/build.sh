#!/bin/sh
# Builds bluetoothd-<version> into deploy/bluez: Raspberry Pi OS's BlueZ
# (source/, the bluez source package as Raspberry Pi OS ships it) with the
# jukebox's fixes (patches/), configured the way the bluez package
# configures bluetoothd. See README.md.
#
# On Raspberry Pi OS trixie (arm64):
#     sh deploy/bluez/build.sh
# It needs build-essential, dpkg-dev, pkg-config and the headers bluetoothd
# builds against: libglib2.0-dev libdbus-1-dev libudev-dev libasound2-dev
# libdw-dev. Without root to install those, SYSROOT=<folder> holding them
# unpacked (apt-get download <package>; dpkg -x <package>.deb <folder>), with
# their lib*.so links pointed at the system's own libraries.
#
# WORK=<folder> builds there (default: a new temporary folder); JOBS=<n>.
set -eu

VERSION=5.82-1.1+rpt2
UPSTREAM=${VERSION%%-*}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HERE}
WORK=${WORK:-$(mktemp -d)}
JOBS=${JOBS:-3}
ARCH=aarch64-linux-gnu

cd "$HERE/source"
sha256sum -c --quiet <<SUMS
818e2bfa64d3d26b49cc857448f00f4fc7bb76949266de8d7f4c07f8057bf95c  bluez_$UPSTREAM.orig.tar.gz
4cfbce4fc558cdbbf2eb919018d4b940830031bf1873845c17879dce2cd41333  bluez_$VERSION.debian.tar.xz
3acd5008bc1c12f323e7af3d58b03366b9b814f2686993c76664c8002697dc93  bluez_$VERSION.dsc
SUMS

mkdir -p "$WORK"
rm -rf "$WORK/bluez"
dpkg-source --no-check --no-copy -x "$HERE/source/bluez_$VERSION.dsc" "$WORK/bluez" >/dev/null
cd "$WORK/bluez"
for p in "$HERE"/patches/*.patch; do
  patch -p1 --no-backup-if-mismatch --quiet < "$p"
done
# One of Debian's patches edits Makefile.am. The Makefile.in it would make
# differs only in installing library headers; keep make from rerunning automake.
touch -r configure.ac Makefile.am

# The bluez package's compiler options (debian/rules).
export DEB_BUILD_MAINT_OPTIONS='hardening=+all optimize=+lto'
export DEB_CFLAGS_MAINT_APPEND='-DFIRMWARE_DIR=/lib/firmware'
eval "$(dpkg-buildflags --export=sh)"
if [ -n "${SYSROOT:-}" ]; then
  export PKG_CONFIG_PATH="$SYSROOT/usr/lib/$ARCH/pkgconfig:$SYSROOT/usr/share/pkgconfig"
  export PKG_CONFIG_SYSROOT_DIR="$SYSROOT"
  # dbus-1.pc lists libsystemd, which bluetoothd doesn't link.
  export DBUS_CFLAGS="-I$SYSROOT/usr/include/dbus-1.0 -I$SYSROOT/usr/lib/$ARCH/dbus-1.0/include"
  export DBUS_LIBS="-L$SYSROOT/usr/lib/$ARCH -ldbus-1"
  # configure looks for elfutils (the backtraces) without pkg-config.
  export CPPFLAGS="$CPPFLAGS -I$SYSROOT/usr/include"
  export LDFLAGS="$LDFLAGS -L$SYSROOT/usr/lib/$ARCH"
fi

# debian/rules' options, less the programs that aren't bluetoothd (obexd,
# bluetooth-meshd, bluetoothctl, btmon, the tools, tests, the CUPS backend).
./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var \
  --libdir="/usr/lib/$ARCH" --libexecdir=/usr/libexec --runstatedir=/run \
  --with-dbusconfdir=/usr/share \
  --with-systemdsystemunitdir=/usr/lib/systemd/system \
  --with-systemduserunitdir=/usr/lib/systemd/user --with-udevdir=/usr/lib/udev \
  --enable-threads --enable-debug --enable-backtrace --enable-nfc --enable-sap \
  --enable-udev --enable-sixaxis --enable-deprecated --enable-health --enable-midi \
  --enable-experimental --enable-systemd --enable-datafiles \
  --disable-obex --disable-mesh --disable-cups --disable-client --disable-tools \
  --disable-monitor --disable-testing --disable-test --disable-library \
  --disable-manpages --disable-hid2hci > configure.log 2>&1 \
  || { tail -20 configure.log >&2; exit 1; }
nice -n 19 make -j"$JOBS" > make.log 2>&1 || { grep -i -A5 'error' make.log | head -40 >&2; exit 1; }

# As dh_strip leaves the package's (debug symbols aside).
strip --remove-section=.comment --remove-section=.note -o "$OUT/bluetoothd-$VERSION" src/bluetoothd
chmod 0755 "$OUT/bluetoothd-$VERSION"
echo "built $OUT/bluetoothd-$VERSION"
sha256sum "$OUT/bluetoothd-$VERSION"
