"""What to do about the network, decided from what jb-netwatch sees.

Pure: no clocks, commands or files. main.py feeds it one observation every
check and carries out the action it returns.

Offline, it climbs one rung at a time, gentlest first:
  1 minute   reconnect   ask NetworkManager to bring the Wi-Fi back
  2 minutes  radio       turn the Wi-Fi off and on
  3 minutes  reboot      restart the jukebox
A hung Wi-Fi driver (the Pi 4's brcmfmac stuck on its SDIO bus, which
nothing short of a restart clears) goes straight to reboot.

A reboot never loops or interrupts a song: not in the first minutes after
startup, spaced out more after each one in a day (15 min, then 30, 60 ... at most
4 h between them), and put off while music plays (up to 10 minutes).
"""

from dataclasses import dataclass, field

RECONNECT_AFTER_S = 60
RADIO_AFTER_S = 120
REBOOT_AFTER_S = 180
# Startup: NetworkManager is still connecting, so nothing is wrong yet.
SETTLE_S = 120
# No reboot sooner than this after starting, hung driver or not.
MIN_UPTIME_FOR_REBOOT_S = 300
# Reboots within this window count toward the spacing.
REBOOT_WINDOW_S = 24 * 3600
FIRST_GAP_S = 15 * 60
MAX_GAP_S = 4 * 3600
# While a song plays, the reboot waits this long at most.
MAX_DEFER_FOR_MUSIC_S = 10 * 60
# Waiting for a reboot to be allowed (or for the router to come back):
# turn the Wi-Fi off and on again this often.
RETRY_RADIO_S = 10 * 60

OK, WAIT, RECONNECT, RADIO, REBOOT, DEFER = "ok", "wait", "reconnect", "radio", "reboot", "defer"


@dataclass
class Policy:
    reboots: list[float] = field(default_factory=list)   # when earlier reboots were made (epoch s)
    offline_since: float | None = None
    reconnected: bool = False
    radio_cycled: bool = False
    reboot_wanted_since: float | None = None
    last_gentle: float = 0.0

    def step(self, now: float, uptime: float, online: bool, driver_hung: bool, playing: bool) -> str:
        if online:
            self.offline_since = None
            self.reconnected = self.radio_cycled = False
            self.reboot_wanted_since = None
            return OK
        if self.offline_since is None:
            self.offline_since = now
        offline_for = now - self.offline_since

        if driver_hung or offline_for >= REBOOT_AFTER_S:
            action = self._reboot(now, uptime, playing)
            if action != WAIT or driver_hung:
                return action   # (a hung driver ignores the gentle steps)
            # Not allowed to reboot yet: keep trying the radio now and then.
            if uptime >= SETTLE_S and now - self.last_gentle >= RETRY_RADIO_S:
                return self._gentle(now, RADIO)
            return WAIT
        if uptime < SETTLE_S:
            return WAIT
        if offline_for >= RADIO_AFTER_S and not self.radio_cycled:
            self.radio_cycled = True
            return self._gentle(now, RADIO)
        if offline_for >= RECONNECT_AFTER_S and not self.reconnected:
            self.reconnected = True
            return self._gentle(now, RECONNECT)
        return WAIT

    def _gentle(self, now: float, action: str) -> str:
        self.last_gentle = now
        return action

    def _reboot(self, now: float, uptime: float, playing: bool) -> str:
        if uptime < MIN_UPTIME_FOR_REBOOT_S or now < self.next_reboot_allowed():
            return WAIT
        if self.reboot_wanted_since is None:
            self.reboot_wanted_since = now
        if playing and now - self.reboot_wanted_since < MAX_DEFER_FOR_MUSIC_S:
            return DEFER
        return REBOOT

    def next_reboot_allowed(self) -> float:
        """The earliest time another reboot may happen. A clock that went
        backwards (no network, no time sync) only ever makes this later."""
        if not self.reboots:
            return 0.0
        last = max(self.reboots)
        recent = [t for t in self.reboots if t > last - REBOOT_WINDOW_S]
        gap = min(FIRST_GAP_S * 2 ** (len(recent) - 1), MAX_GAP_S)
        return last + gap

    def rebooted(self, now: float) -> None:
        self.reboots = [t for t in self.reboots if t > now - REBOOT_WINDOW_S] + [now]


def driver_hung(kernel_log: str) -> bool:
    """True if the kernel reported a task stuck in the Wi-Fi driver: a
    'blocked for more than' report whose trace runs through brcmfmac."""
    lines = kernel_log.splitlines()
    for i, line in enumerate(lines):
        if "blocked for more than" in line:
            for after in lines[i + 1:i + 40]:
                if "blocked for more than" in after:
                    break
                if "brcmf" in after:
                    return True
    return False


def is_online(nm_state: str | None, gateway: str | None, ping_ok: bool, gateway_ever_answered: bool) -> bool:
    """Online: NetworkManager says connected and the router answers. A router
    that never answers pings (some don't) isn't held against us."""
    if not nm_state or not nm_state.startswith("connected") or not gateway:
        return False
    return ping_ok or not gateway_ever_answered
