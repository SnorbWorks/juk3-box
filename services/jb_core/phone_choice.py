"""Which phone has the jukebox: one at a time, Bluetooth or the helper app.

Both kinds can be connected at once (Bluetooth for one phone, the app over
Wi-Fi for another); the board shows one phone's songs, and picks go to it.
USB drives aren't in this: their songs are always on the board.

- Nobody has it: the first phone to arrive gets it.
- Someone has it: a phone arriving doesn't take over. The cabinet offers it
  ("Alex's iPhone is here. Switch to it?"), and Sources switches any time.
- The one that has it leaves: after GRACE_S (a Bluetooth phone drops out of
  range for a moment and comes back), the latest other phone still here
  takes over.

Published retained as jukebox/jb-core/source; jb-library shows that phone's
songs and jb-source-helper makes that app phone its source. Pure, for tests:
main.py feeds it the sources' messages and carries out what it returns.
"""

GRACE_S = 60.0
PHONE, HELPER = "phone", "helper"


class PhoneChoice:
    def __init__(self):
        self.here: dict[str, dict] = {}         # id -> {kind, id, name}, connected now, in arrival order
        self.names: dict[str, tuple[str, str]] = {}   # id -> (kind, name), every paired phone
        self.active: dict | None = None
        self.offer: dict | None = None
        self.declined: set[str] = set()
        self.gone_since: float | None = None
        # Just started: the sources' retained messages and our own last
        # choice arrive in any order. Note who's here, choose nobody and
        # offer nothing until settled(), so a restart doesn't ask again.
        self.settling = True

    # -- what the sources say ----------------------------------------------

    def bluetooth(self, payload: dict, now: float) -> list:
        """jukebox/jb-source-phone/connected (one Bluetooth phone at most)."""
        mac = payload.get("mac")
        if mac and payload.get("name"):
            self.names[mac] = (PHONE, payload["name"])
        here = {mac: payload.get("name")} if payload.get("connected") and mac else {}
        return self._update(PHONE, here, now)

    def bluetooth_phones(self, phones: list) -> None:
        """jukebox/jb-source-phone/phones: names, for switching to one not here."""
        for p in phones or []:
            if p.get("mac"):
                self.names[p["mac"]] = (PHONE, p.get("name") or "Phone")

    def helper(self, payload: dict, now: float) -> list:
        """jukebox/jb-source-helper/connected: each app phone, reachable or not
        (asleep counts: it's woken for a pick)."""
        here = {}
        for p in payload.get("phones") or []:
            pid = f"helper:{p.get('device_id')}"
            self.names[pid] = (HELPER, p.get("name") or "iPhone")
            if p.get("reachable"):
                here[pid] = p.get("name") or "iPhone"
        listed = {f"helper:{p.get('device_id')}" for p in payload.get("phones") or []}
        actions = self._update(HELPER, here, now)
        for pid in [i for i, (kind, _) in self.names.items() if kind == HELPER and i not in listed]:
            actions += self.forgotten(pid, now)
        return actions

    def forgotten(self, pid: str, now: float) -> list:
        """A phone unpaired: if it had the jukebox, the latest one here gets it."""
        self.names.pop(pid, None)
        self.here.pop(pid, None)
        if self.offer and self.offer["id"] == pid:
            self.offer = None
        if not (self.active and self.active["id"] == pid):
            return []
        latest = self._latest()
        return self._activate(latest, now) if latest else self._clear()

    def _update(self, kind: str, here: dict, now: float) -> list:
        actions = []
        for pid in [i for i, c in self.here.items() if c["kind"] == kind and i not in here]:
            del self.here[pid]
            self.declined.discard(pid)
            if self.offer and self.offer["id"] == pid:
                self.offer = None
                actions.append(("changed",))
            if self.active and self.active["id"] == pid:
                self.gone_since = now
                actions.append(("changed",))
        for pid, name in here.items():
            if pid in self.here:
                if name and self.here[pid]["name"] != name:     # renamed
                    self.here[pid]["name"] = name
                    if self.active and self.active["id"] == pid:
                        self.active["name"] = name
                    actions.append(("changed",))
                continue
            c = {"kind": kind, "id": pid, "name": name or self.names.get(pid, (kind, "Phone"))[1]}
            self.here[pid] = c
            actions += self._arrived(c, now)
        return actions + self.tick(now)

    def _arrived(self, c: dict, now: float) -> list:
        if self.settling:
            return []
        if self.active is None:
            return self._activate(c, now)
        if c["id"] == self.active["id"]:
            self.gone_since = None
            return [("changed",)]
        if c["id"] not in self.declined:
            self.offer = c
            return [("changed",)]
        return []

    def settled(self, now: float) -> list:
        self.settling = False
        if self.active and self.active["id"] in self.here:
            self.gone_since = None
        return [("changed",)] + self.tick(now)

    def tick(self, now: float) -> list:
        if self.settling:
            return []
        if self.active is None:
            latest = self._latest()
            return self._activate(latest, now) if latest else []
        if self.active["id"] not in self.here and self.gone_since is not None and now - self.gone_since >= GRACE_S:
            latest = self._latest()
            if latest:
                return self._activate(latest, now)
        return []

    def _latest(self) -> dict | None:
        return list(self.here.values())[-1] if self.here else None

    # -- the cabinet and the admin -------------------------------------------

    def use(self, pid: str, now: float) -> list:
        """Sources: switch to this phone, here or not (a Bluetooth phone is
        then connected; an app phone is woken for its first pick)."""
        if pid in self.here:
            return self._activate(self.here[pid], now)
        if pid in self.names:
            kind, name = self.names[pid]
            return self._activate({"kind": kind, "id": pid, "name": name}, now)
        return []

    def decline(self) -> list:
        if not self.offer:
            return []
        self.declined.add(self.offer["id"])
        self.offer = None
        return [("changed",)]

    def restore(self, retained: dict, now: float) -> None:
        """Our own retained choice, after a restart."""
        a = (retained or {}).get("active")
        if self.active is None and isinstance(a, dict) and a.get("id") and a.get("kind") in (PHONE, HELPER):
            self.active = {"kind": a["kind"], "id": a["id"], "name": a.get("name") or "Phone"}
            self.gone_since = None if a["id"] in self.here else now

    def _activate(self, c: dict, now: float) -> list:
        old = self.active
        self.active = {"kind": c["kind"], "id": c["id"], "name": c["name"]}
        self.gone_since = None if c["id"] in self.here else now
        if self.offer and self.offer["id"] == c["id"]:
            self.offer = None
        self.declined.discard(c["id"])
        actions = [("changed",)]
        if old is None or old["id"] != c["id"]:
            actions.append(("switched", old, self.active))
            if c["kind"] == PHONE and c["id"] not in self.here:
                actions.append(("connect_bluetooth", c["id"]))
        return actions

    def _clear(self) -> list:
        old, self.active, self.gone_since = self.active, None, None
        return [("changed",), ("switched", old, None)] if old else []

    def state(self) -> dict:
        active = None
        if self.active:
            active = {**self.active, "connected": self.active["id"] in self.here}
        return {"active": active, "offer": self.offer}
