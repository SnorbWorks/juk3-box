#!/bin/sh
# Builds the jukebox's AirPlay 2 receiver into deploy/airplay: Shairport Sync
# with AirPlay 2, and NQPTP (the clock AirPlay 2 needs), from the release
# tarballs in source/. See README.md.
#
# On Raspberry Pi OS trixie (arm64):
#     sh deploy/airplay/build.sh
# It needs a compiler (build-essential, as the jukebox has), pkg-config, and
# the headers it builds against: libpopt-dev libconfig-dev libssl-dev
# libsoxr-dev libplist-dev libsodium-dev libavutil-dev libavcodec-dev
# libavformat-dev libswresample-dev uuid-dev libgcrypt20-dev libgpg-error-dev
# libpulse-dev libavahi-client-dev libavahi-common-dev, plus xxd and
# plistutil (libplist-utils). Without root to install those, SYSROOT=<folder>
# holding them unpacked (apt-get download <package>; dpkg -x <package>.deb
# <folder>), with their lib*.so links pointed at the system's own libraries.
#
# The tarballs are plain git exports: autoreconf -fi makes their configure
# scripts. The jukebox has no autoconf/automake/libtool; with them on another
# computer, PREPARED=<folder> holds both trees already autoreconf'd (the
# folder names as in the tarballs) and the build uses those.
#
# WORK=<folder> builds there (default: a new temporary folder); JOBS=<n>.
set -eu

SHAIRPORT=5.6.3
NQPTP=1.2.9
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HERE}
WORK=${WORK:-$(mktemp -d)}
JOBS=${JOBS:-3}
ARCH=aarch64-linux-gnu

cd "$HERE/source"
sha256sum -c --quiet <<SUMS
670f9cefdb9c2d70f7df71890e63ab0a31d08a815dd0734245d935bfd01e766e  shairport-sync-$SHAIRPORT.tar.gz
6aa1d73b8ee760bf19378c319db0d0537a867ff961764b150e19f87d96a02210  nqptp-$NQPTP.tar.gz
SUMS

mkdir -p "$WORK"
cd "$WORK"
rm -rf "shairport-sync-$SHAIRPORT" "nqptp-$NQPTP"
for t in "shairport-sync-$SHAIRPORT" "nqptp-$NQPTP"; do
  if [ -n "${PREPARED:-}" ]; then
    cp -R "$PREPARED/$t" .
  else
    tar -xzf "$HERE/source/$t.tar.gz"
    (cd "$t" && autoreconf -fi)
  fi
done

# Debian's hardened compiler options, as the system's own packages use.
export DEB_BUILD_MAINT_OPTIONS='hardening=+all'
eval "$(dpkg-buildflags --export=sh)"
if [ -n "${SYSROOT:-}" ]; then
  export PATH="$SYSROOT/usr/bin:$PATH"
  export PKG_CONFIG_PATH="$SYSROOT/usr/lib/$ARCH/pkgconfig:$SYSROOT/usr/share/pkgconfig"
  export PKG_CONFIG_SYSROOT_DIR="$SYSROOT"
  export CPPFLAGS="$CPPFLAGS -I$SYSROOT/usr/include -I$SYSROOT/usr/include/$ARCH"
  export LDFLAGS="$LDFLAGS -L$SYSROOT/usr/lib/$ARCH"
fi

cd "$WORK/nqptp-$NQPTP"
./configure --with-systemd-startup=no
make -j"$JOBS"
install -m 0755 nqptp "$OUT/nqptp-$NQPTP"

# AirPlay 2, into PulseAudio (PipeWire's pulse server, like the classic
# receiver), FFmpeg's ALAC decoder; the metadata pipe and stdout/pipe outputs
# as Raspberry Pi OS's package has them. No MQTT/D-Bus/MPRIS: nothing uses them.
cd "$WORK/shairport-sync-$SHAIRPORT"
./configure --sysconfdir=/etc --with-airplay-2 --with-avahi --with-ssl=openssl --with-soxr \
  --with-pulseaudio --with-ffmpeg --with-stdout --with-pipe --with-metadata
make -j"$JOBS"
install -m 0755 shairport-sync "$OUT/shairport-sync-$SHAIRPORT"

"$OUT/shairport-sync-$SHAIRPORT" -V
"$OUT/nqptp-$NQPTP" -V
