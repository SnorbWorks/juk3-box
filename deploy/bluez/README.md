# bluetoothd with the jukebox's AVRCP fixes

`bluetoothd-5.82-1.1+rpt2` is the Bluetooth daemon of Raspberry Pi OS's
`bluez` package (5.82-1.1+rpt2), rebuilt with the two patches in `patches/`
and configured as the package configures it. Nothing else differs: same
compiler, options, built-in plugins and libraries.

## Why

To browse a phone's songs, BlueZ moves through the phone's folders
(AVRCP ChangePath). After each move it asks the folder's item count
(GetTotalNumberOfItems), with a malformed command: it says it has 11 bytes
of parameters, sends 7, and the specification gives it one (the scope).
An iPhone never answers it, so the next browsing command waits out BlueZ's
10-second browsing timeout. Measured on the cabinet, 2026-11-01: 10.15 s
and 9.92 s for a step that otherwise takes 0.03 s. Getting into Songs took
over 20 s on every connect. The bug is the same in 5.66 and in upstream
master. Reported upstream on 2026-11-01, with the evidence and both
patches: https://github.com/bluez/bluez/issues/2745

- `0001` sends the command with its one parameter, and makes the response
  handler accept what it should and reject what it should.
- `0002` stops asking for the count after SetBrowsedPlayer and ChangePath
  in the filesystem: their responses already carry it.

## How it runs

`install.sh` puts the build in `/usr/local/lib/juk3-box/bluez/`,
`jb-bluetoothd` in `/usr/local/sbin/`, and
`deploy/systemd/bluetooth.service.d/50-juk3box-bluez.conf`, which starts
bluetoothd through `jb-bluetoothd`. That runs ours only when it was built
from the BlueZ that's installed. Otherwise it runs the system's own,
`/usr/libexec/bluetooth/bluetoothd`, because a newer BlueZ may carry fixes
ours lacks; without ours, browsing is only slower. It also runs the
system's own:

- after ours stopped with a failure, until the Pi next starts
  (`/run/jb-bluetoothd.failed`);
- when chosen: `jb-setupctl bluez stock` (back: `jb-setupctl bluez fixed`).
  This restarts Bluetooth now, and the choice is kept across restarts.

Its journal line says which one runs: `journalctl -u bluetooth | grep jb-bluetoothd`.

## Rebuilding

On Raspberry Pi OS trixie (arm64), `sh deploy/bluez/build.sh`. Its header
lists what it needs. For a new BlueZ:

1. Put the new source package in `source/`. It's the `.dsc`,
   `.debian.tar.xz` and `.orig.tar.gz` from
   http://archive.raspberrypi.com/debian/pool/main/b/bluez/.
2. Set `VERSION` and the checksums in `build.sh`, then build.
3. Commit the new `bluetoothd-<version>` and remove the old one.

## License

BlueZ is GPL-2.0-or-later. `source/`, `patches/` and `build.sh` are the
complete source of `bluetoothd-5.82-1.1+rpt2`, and they ship with it.
