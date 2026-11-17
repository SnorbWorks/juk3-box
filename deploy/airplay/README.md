# The AirPlay receiver: AirPlay 2

The jukebox's AirPlay speaker (jb-airplay) is where the Juk3box Helper app's
songs come in, from an iPhone or an Apple TV. Since v0.4.2 it's AirPlay 2:

- `shairport-sync-5.6.3`: Shairport Sync 5.6.3 built with AirPlay 2
  (into PipeWire's PulseAudio server, FFmpeg's ALAC decoder).
- `nqptp-1.2.9`: NQPTP 1.2.9, the network clock AirPlay 2 needs
  (jb-nqptp).

## Why

Raspberry Pi OS's `shairport-sync` package is classic AirPlay (AirPlay 1)
only. An iPhone plays Apple Music to it, but an **Apple TV doesn't**: it
starts the song and cancels it about 9 s later
(`MPMusicPlayerControllerErrorDomain` 6, "cancelled"). Found 2026-11-16 with
the Helper app's Apple TV test build: it played on the TV's own speakers and
failed every time it was sent to the jukebox. AirPlay 2 was the plan from the
start (docs/JUK3BOX_HELPER_BUILD_PLAN.md); it waited for the Pi 5.

## How it runs

`install.sh` (deploy/install) puts the builds in
`/usr/local/lib/juk3-box/airplay/` (as `shairport-sync` and `nqptp`) and
`jb-airplay` in `/usr/local/sbin/`. `jb-airplay.service` starts the receiver
through `jb-airplay`, which runs ours with `shairport-sync-ap2.conf`, or
Raspberry Pi OS's own (`/usr/bin/shairport-sync`, classic) with
`shairport-sync.conf`:

- when `/etc/juk3-box/airplay-classic` exists: `jb-setupctl airplay classic`
  (back: `jb-setupctl airplay ap2`; both restart the receiver now and the
  choice is kept across restarts);
- after ours stopped with a failure, until the Pi next starts
  (`/run/jb-airplay.failed`);
- while jb-nqptp isn't running;
- when ours is missing a library (an OS update replaced one).

Even running, ours drops to classic AirPlay by itself if it can't find NQPTP.
Its journal line says which one runs: `journalctl -u jb-airplay | grep jb-airplay:`.

jb-nqptp runs NQPTP as admin with `CAP_NET_BIND_SERVICE` (PTP's ports 319
and 320); the receiver, also admin, reads its clock from `/dev/shm/nqptp`.

## Updates

A newer Shairport Sync reaches the jukebox only in a Juk3box release, built
and tested here: the two programs and the config have to match. The admin's
Updates tab lists ours under "What's inside" and notes when a newer release is
out (jb-update's check reads Shairport Sync's latest GitHub release).

## Rebuilding

On Raspberry Pi OS trixie (arm64), `sh deploy/airplay/build.sh`. Its header
lists what it needs. The jukebox itself has no root for `apt-get install`,
and no autoconf: the 5.6.3/1.2.9 builds were made on the cabinet with
`SYSROOT` (the `-dev` packages unpacked with `apt-get download` + `dpkg -x`)
and `PREPARED` (the trees `autoreconf -fi`'d on a Mac with Homebrew's
autoconf, automake and libtool). For a new version:

1. Put the release tarballs from GitHub (`archive/refs/tags/<version>.tar.gz`)
   in `source/`, named as the old ones.
2. Set the versions and the checksums in `build.sh`, then build.
3. Commit the new `shairport-sync-<version>` / `nqptp-<version>`, remove the
   old ones, and read the release notes for config changes
   (`shairport-sync-ap2.conf`).

## License

Shairport Sync: MIT and other permissive licenses, file by file
(`LICENSES-shairport-sync.txt`). NQPTP: GPL-2.0 (`LICENSE-nqptp.txt`).
`source/` and `build.sh` are the complete source of both builds, and they ship
with them.
