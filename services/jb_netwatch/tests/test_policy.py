from services.jb_netwatch import policy
from services.jb_netwatch.policy import DEFER, OK, RADIO, REBOOT, RECONNECT, WAIT, Policy

UP = 3600.0   # well past startup


def walk(p, start, until, step=30, uptime=UP, hung=False, playing=False):
    """Offline from `start` to `until`: the actions taken, with when."""
    out = []
    t = start
    while t <= until:
        a = p.step(t, uptime + (t - start), False, hung, playing)
        if a != WAIT:
            out.append((t - start, a))
        t += step
    return out


def test_online_does_nothing():
    p = Policy()
    assert p.step(1000, UP, True, False, False) == OK


def test_climbs_gently_then_reboots():
    p = Policy()
    assert walk(p, 1000, 1000 + 200) == [(60, RECONNECT), (120, RADIO), (180, REBOOT)]


def test_coming_back_resets_the_climb():
    p = Policy()
    walk(p, 1000, 1000 + 90)
    assert p.step(1100, UP, True, False, False) == OK
    assert walk(p, 2000, 2000 + 70) == [(60, RECONNECT)]


def test_hung_driver_reboots_at_once():
    p = Policy()
    assert p.step(1000, UP, False, True, False) == REBOOT


def test_no_reboot_right_after_startup():
    p = Policy()
    assert p.step(1000, 100, False, True, False) == WAIT
    assert p.step(1030, policy.MIN_UPTIME_FOR_REBOOT_S, False, True, False) == REBOOT


def test_nothing_while_starting_up():
    # Offline from the start: NetworkManager is still connecting.
    p = Policy()
    assert walk(p, 0, 90, uptime=10) == []


def test_reboots_are_spaced_further_apart():
    p = Policy(reboots=[1000.0])
    assert p.next_reboot_allowed() == 1000 + 15 * 60
    p = Policy(reboots=[1000.0, 2000.0])
    assert p.next_reboot_allowed() == 2000 + 30 * 60
    p = Policy(reboots=[1000.0 + i for i in range(20)])
    assert p.next_reboot_allowed() == 1019 + policy.MAX_GAP_S
    # Old ones stop counting.
    p = Policy(reboots=[0.0, 30 * 3600.0])
    assert p.next_reboot_allowed() == 30 * 3600 + 15 * 60


def test_router_down_never_loops():
    # Offline for a whole day: reboots stay spaced out, the radio keeps
    # being tried in between.
    p = Policy()
    reboots, radios = [], 0
    t, up = 0.0, UP
    while t < 24 * 3600:
        a = p.step(t, up, False, False, False)
        if a == REBOOT:
            reboots.append(t)
            p.rebooted(t)
            up = 0.0
        radios += a == RADIO
        t += 30
        up += 30
    gaps = [b - a for a, b in zip(reboots, reboots[1:])]
    assert len(reboots) <= 10
    assert all(g >= 15 * 60 for g in gaps)
    assert radios > len(reboots)


def test_waits_for_the_song_then_gives_up_waiting():
    p = Policy()
    assert p.step(1000, UP, False, True, True) == DEFER
    assert p.step(1000 + 300, UP, False, True, True) == DEFER
    assert p.step(1000 + policy.MAX_DEFER_FOR_MUSIC_S, UP, False, True, True) == REBOOT


def test_music_stopping_lets_it_reboot():
    p = Policy()
    assert p.step(1000, UP, False, True, True) == DEFER
    assert p.step(1030, UP, False, True, False) == REBOOT


def test_clock_behind_only_delays():
    # After a restart with no network the clock can be behind the last reboot.
    p = Policy(reboots=[5000.0])
    assert p.step(4000, UP, False, True, False) == WAIT


HUNG_LOG = """\
brcmfmac: brcmf_cfg80211_set_power_mgmt: power save disabled
INFO: task kworker/u17:3:541 blocked for more than 120 seconds.
      Tainted: G         C          6.18.50+rpt-rpi-v8 #1
Workqueue: brcmf_wq/mmc1:0001:1 brcmf_sdio_dataworker [brcmfmac]
Call trace:
 __switch_to+0xf0/0x1c8 (T)
"""

OTHER_HANG = """\
brcmfmac: brcmf_c_preinit_dcmds: Firmware: BCM4345/6
INFO: task jbd2/mmcblk0p2:120 blocked for more than 120 seconds.
Call trace:
 __switch_to+0xf0/0x1c8 (T)
 jbd2_journal_commit_transaction+0x1/0x2
"""


def test_spots_a_hung_wifi_driver():
    assert policy.driver_hung(HUNG_LOG)
    assert not policy.driver_hung("brcmfmac: power save disabled\nall fine\n")
    # A hang elsewhere isn't the Wi-Fi's, even with brcmfmac lines before it.
    assert not policy.driver_hung(OTHER_HANG)


def test_online_needs_the_router():
    assert policy.is_online("connected", "10.0.0.1", True, True)
    assert not policy.is_online("connected", "10.0.0.1", False, True)
    assert not policy.is_online("disconnected", "10.0.0.1", True, True)
    assert not policy.is_online("connected", None, False, False)
    assert not policy.is_online(None, "10.0.0.1", True, True)
    # A router that has never answered a ping isn't held against us.
    assert policy.is_online("connected", "10.0.0.1", False, False)
    assert policy.is_online("connected (site only)", "10.0.0.1", True, True)
