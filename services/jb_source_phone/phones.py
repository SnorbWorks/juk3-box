"""Which phone has the jukebox (dev plan Phase 4: connection policy).

Locked (the user's choice, 2026-10-14): one phone at a time. While it's
connected, any other paired phone that connects from its own Bluetooth
settings is turned away. When it drops mid-song its place is held for the
reconnect window (reconnect.DROP_WINDOW_S), so a guest can't slip in while
the owner steps out of range; when it drops while idle, or the hold runs
out, the jukebox is open and the first paired phone to connect gets it.
Switching on purpose (the jukebox's own menu or the admin, or pairing a new
phone there) always works.

Pure: main.py reports what's connected and carries out the turn-aways.
"""

from dataclasses import dataclass

# A switch on purpose holds the place for the phone being connected to,
# so it isn't turned away for arriving while the old one is still leaving.
SWITCH_HOLD_S = 60


@dataclass
class Lock:
    owner: str | None = None        # the phone (MAC) with the jukebox
    held_until: float = 0.0         # its place is kept until then, while it's away

    def update(self, connected: list[str], now: float, recent_first: list[str] | None = None) -> list[str]:
        """Given the phones connected now, the ones to turn away. With no
        owner and no hold, the first to connect (most recently used first,
        if several arrived together) becomes the owner."""
        if self.owner in connected:
            self.held_until = 0.0
            return [m for m in connected if m != self.owner]
        if self.owner is not None and now < self.held_until:
            return list(connected)
        if not connected:
            return []
        order = [m for m in (recent_first or []) if m in connected] + [m for m in connected if m not in (recent_first or [])]
        self.owner, self.held_until = order[0], 0.0
        return order[1:]

    def dropped(self, mac: str, now: float, hold_s: float | None) -> None:
        """The owner went away: hold its place for hold_s (a drop mid-song),
        or free it (hold_s None: dropped while idle)."""
        if mac != self.owner:
            return
        if hold_s:
            self.held_until = now + hold_s
        else:
            self.owner, self.held_until = None, 0.0

    def switch_to(self, mac: str, now: float) -> None:
        self.owner, self.held_until = mac, now + SWITCH_HOLD_S

    def release(self) -> None:
        """Disconnected on purpose: the jukebox is open."""
        self.owner, self.held_until = None, 0.0

    def forget(self, mac: str) -> None:
        if mac == self.owner:
            self.release()


def phone_list(paired: list[tuple], book: dict, owner: str | None) -> list[dict]:
    """The phones for the screens, most recently used first: paired is
    reconnect.phones()' [(mac, path, name, connected)], book the PhoneBook's
    {mac: {name, last_connected}}."""
    out = [{"mac": mac, "name": name, "connected": bool(connected), "active": mac == owner,
            "last_connected": (book.get(mac) or {}).get("last_connected")}
           for mac, _path, name, connected in paired]
    return sorted(out, key=lambda p: (not p["connected"], -(p["last_connected"] or 0), p["name"].lower()))
