"""Is an app's device the one sending AirPlay to this jukebox?

The app can't always tell: an iPhone names its AirPlay speaker ("Juk3box"),
but an Apple TV reports every AirPlay output as just "AirPlay". The jukebox
can, though: its AirPlay receiver (jb-airplay) has a TCP connection from the
device that streams to it, open for as long as that device has it chosen.
That connection and the app's WebSocket come from the same device, though
not always from the same address (IPv4 vs IPv6, and IPv6 addresses
rotate), so they're matched by address, or else by the hardware address the
neighbour table gives both.

Reads /proc/net/tcp{,6} and `ip -j neigh`: no root needed.
"""

import ipaddress
import json
import subprocess
from pathlib import Path

# jb-airplay's RTSP port: 7000 for AirPlay 2, 5000 for classic AirPlay.
AIRPLAY_PORTS = (7000, 5000)
ESTABLISHED = "01"


def _ip(text: str) -> str | None:
    """A plain address: no zone, no port, IPv4-mapped IPv6 as IPv4."""
    if not text:
        return None
    text = text.split("%")[0]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return str(addr)


def _proc_address(hex_addr: str) -> str | None:
    """/proc/net/tcp{,6}'s address: 32-bit words in host (little-endian) order."""
    raw = bytes.fromhex(hex_addr)
    if len(raw) not in (4, 16):
        return None
    flipped = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    return _ip(str(ipaddress.ip_address(flipped)))


def airplay_peers(proc: Path = Path("/proc/net"), ports=AIRPLAY_PORTS) -> set[str]:
    """The addresses with an open connection to the AirPlay receiver."""
    peers = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = (proc / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != ESTABLISHED:
                continue
            local, remote = fields[1], fields[2]
            try:
                if int(local.rsplit(":", 1)[1], 16) not in ports:
                    continue
                peer = _proc_address(remote.rsplit(":", 1)[0])
            except ValueError:
                continue
            if peer:
                peers.add(peer)
    return peers


def neighbours(run=subprocess.run) -> dict[str, str]:
    """Address -> hardware address, from the kernel's neighbour table."""
    try:
        out = run(["ip", "-j", "neigh", "show"], capture_output=True, text=True, timeout=3).stdout
        entries = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    macs = {}
    for e in entries:
        ip, mac = _ip(str(e.get("dst") or "")), e.get("lladdr")
        if ip and mac and "FAILED" not in (e.get("state") or []):
            macs[ip] = mac.lower()
    return macs


def same_device(a: str | None, b: str | None, macs: dict[str, str]) -> bool:
    a, b = _ip(a or ""), _ip(b or "")
    if not a or not b:
        return False
    if a == b:
        return True
    return macs.get(a) is not None and macs.get(a) == macs.get(b)


def hears(device_ip: str | None, peers: set[str], macs: dict[str, str]) -> bool:
    """True when the device at `device_ip` is streaming AirPlay to the jukebox."""
    return any(same_device(device_ip, peer, macs) for peer in peers)
