-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Format: 3.0 (quilt)
Source: bluez
Binary: libbluetooth3, libbluetooth-dev, bluetooth, bluez, bluez-cups, bluez-obexd, bluez-meshd, bluez-hcidump, bluez-test-tools, bluez-test-scripts, bluez-source
Architecture: linux-any all
Version: 5.82-1.1+rpt2
Maintainer: Debian Bluetooth Maintainers <team+pkg-bluetooth@tracker.debian.org>
Uploaders: Nobuhiro Iwamatsu <iwamatsu@debian.org>
Homepage: http://www.bluez.org
Standards-Version: 4.7.0
Vcs-Browser: https://salsa.debian.org/bluetooth-team/bluez
Vcs-Git: https://salsa.debian.org/bluetooth-team/bluez.git
Testsuite: autopkgtest
Testsuite-Triggers: python3-dbus
Build-Depends: debhelper-compat (= 13), flex, bison, libdbus-1-dev (>= 1.6), libebook1.2-dev [!i386 !alpha !m68k !ppc64 !sh4 !x32], libglib2.0-dev, libdw-dev, libudev-dev, libreadline-dev, libical-dev, libasound2-dev, libell-dev (>= 0.39), libjson-c-dev (>= 0.13), python3-docutils, python3-pygments, udev, check <!nocheck>, systemd-dev
Package-List:
 bluetooth deb admin optional arch=all
 bluez deb admin optional arch=linux-any
 bluez-cups deb admin optional arch=linux-any
 bluez-hcidump deb admin optional arch=linux-any
 bluez-meshd deb admin optional arch=linux-any
 bluez-obexd deb admin optional arch=linux-any
 bluez-source deb admin optional arch=all
 bluez-test-scripts deb admin optional arch=all
 bluez-test-tools deb admin optional arch=linux-any
 libbluetooth-dev deb libdevel optional arch=linux-any
 libbluetooth3 deb libs optional arch=linux-any
Checksums-Sha1:
 0bb92dc27b684d818d222d58b7c715860829fe7a 3680528 bluez_5.82.orig.tar.gz
 eb2ac094cce0ec1020ac1dd7ef26776cd58fae72 46476 bluez_5.82-1.1+rpt2.debian.tar.xz
Checksums-Sha256:
 818e2bfa64d3d26b49cc857448f00f4fc7bb76949266de8d7f4c07f8057bf95c 3680528 bluez_5.82.orig.tar.gz
 4cfbce4fc558cdbbf2eb919018d4b940830031bf1873845c17879dce2cd41333 46476 bluez_5.82-1.1+rpt2.debian.tar.xz
Files:
 624ed5ee10f61e82410a38daae7ec290 3680528 bluez_5.82.orig.tar.gz
 a5309d0f86832a15972554bf542fc1d7 46476 bluez_5.82-1.1+rpt2.debian.tar.xz

-----BEGIN PGP SIGNATURE-----

iQFKBAEBCgA0FiEESAIDyRDaKQxE9wTWpByCiFSEpSUFAmp5+J4WHHNlcmdlQHJh
c3BiZXJyeXBpLmNvbQAKCRCkHIKIVISlJbRtB/9cZCFqVYFXgkRwbgm4TuSbnP9x
CzHvmHadZpGW3cUrwBBC7nT2wD6cijW2jRD3+NaL3cC82xISJeVs4IwDOE+OU9dB
0/Cx72v30/uT0POFH/PD8LxPs5GbpOceA9Y5sjRQjapAYpdWiqifaX9a5EZRjbGn
W5/0K79oygSkA58MCRqbqWL7gmAOR1+mpGh8bZBOZq9IfrqAmvFng2a5JnDNQKUs
eem+W3bc3wfv7X6CGtPjk8XpwHDeNMc5/lFK7w0OOGsfjwLXankJ90clwnNKu+Cc
KiekF7wwCpEH2qAxPOT91rZwug3qaF+ww5mJdvx0+XOGzgO6GMtyIYgtN5yU
=kvPf
-----END PGP SIGNATURE-----
