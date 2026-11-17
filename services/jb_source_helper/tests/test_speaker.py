"""speaker.py: which device sends AirPlay to the jukebox, and the app hearing it."""

import asyncio
import json
import subprocess

from services.jb_source_helper import speaker
from services.jb_source_helper.tests.test_helper import pair, run

HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def tcp_line(local: str, remote: str, state: str = "01") -> str:
    return f"   0: {local} {remote} {state} 00000000:00000000 00:00000000 00000000  1000        0 1\n"


def proc(tmp_path, tcp=(), tcp6=()):
    (tmp_path / "tcp").write_text(HEADER + "".join(tcp))
    (tmp_path / "tcp6").write_text(HEADER + "".join(tcp6))
    return tmp_path


# 10.0.0.106 is 6A00000A in /proc's byte order; port 7000 is 1B58, 5000 is 1388, 8770 is 2242.
V4_PEER = "6A00000A"
# 2601:1c0:4301:7b07:85a3:d722:b8bc:f6aa, each 32-bit word byte-swapped.
V6_PEER = "C0010126077B0143" + "22D7A385AAF6BCB8"
SELF6 = "0" * 32


def test_the_receivers_connections_are_the_peers(tmp_path):
    p = proc(tmp_path,
             tcp=[tcp_line("6400000A:1B58", f"{V4_PEER}:C350"),            # AirPlay 2, from 10.0.0.106
                  tcp_line("6400000A:2242", "0200000A:C351"),              # the helper's own port: not AirPlay
                  tcp_line("6400000A:1388", "0300000A:C352", state="06")], # closed (TIME_WAIT)
             tcp6=[tcp_line(f"{SELF6}:1B58", f"{V6_PEER}:C0EE"),
                   tcp_line(f"{SELF6}:1B58", "0000000000000000FFFF00000400000A:C0EF")])   # IPv4-mapped 10.0.0.4
    assert speaker.airplay_peers(p) == {"10.0.0.106", "2601:1c0:4301:7b07:85a3:d722:b8bc:f6aa", "10.0.0.4"}


def test_no_proc_files_means_no_peers(tmp_path):
    assert speaker.airplay_peers(tmp_path) == set()


def test_neighbours_from_ip_neigh():
    out = json.dumps([
        {"dst": "10.0.0.106", "dev": "wlan0", "lladdr": "C8:D0:83:E2:7C:35", "state": ["REACHABLE"]},
        {"dst": "2601:1c0:4301:7b07:85a3:d722:b8bc:f6aa", "lladdr": "c8:d0:83:e2:7c:35", "state": ["STALE"]},
        {"dst": "10.0.0.9", "state": ["FAILED"]},
        {"dst": "10.0.0.8", "lladdr": "aa:bb:cc:dd:ee:ff", "state": ["FAILED"]}])
    def fake(cmd, **kw):
        assert cmd == ["ip", "-j", "neigh", "show"]
        return subprocess.CompletedProcess(cmd, 0, stdout=out)
    assert speaker.neighbours(fake) == {"10.0.0.106": "c8:d0:83:e2:7c:35",
                                        "2601:1c0:4301:7b07:85a3:d722:b8bc:f6aa": "c8:d0:83:e2:7c:35"}


def test_neighbours_without_ip_is_empty():
    def broken(cmd, **kw):
        raise FileNotFoundError("ip")
    assert speaker.neighbours(broken) == {}


def test_the_same_device_by_address_or_by_hardware_address():
    macs = {"10.0.0.106": "c8:d0", "2601::f6aa": "c8:d0", "10.0.0.50": "aa:bb"}
    assert speaker.same_device("10.0.0.106", "::ffff:10.0.0.106", {})          # one address, two spellings
    assert speaker.same_device("10.0.0.106", "2601::f6aa", macs)               # IPv4 app, IPv6 AirPlay
    assert not speaker.same_device("10.0.0.106", "10.0.0.50", macs)            # another device
    assert not speaker.same_device("10.0.0.7", "10.0.0.8", macs)               # unknown: not the same
    assert not speaker.same_device(None, "10.0.0.8", macs)
    assert speaker.hears("10.0.0.106", {"10.0.0.50", "2601::f6aa"}, macs)
    assert not speaker.hears("10.0.0.106", set(), macs)


def test_an_app_that_asks_hears_when_its_device_starts_and_stops_sending_airplay(tmp_path):
    async def check(client, helper, bus):
        peers = set()
        helper.airplay_peers = lambda: set(peers)
        helper.neighbours = lambda: {"127.0.0.1": "c8:d0", "2601::f6aa": "c8:d0"}
        real = helper.check_speakers
        async def later():
            pass
        helper.check_speakers = later                          # pairing first (test_helper's steps)
        app, _ = await pair(client, helper, bus, features=["speaker"])
        helper.check_speakers = real
        await helper.check_speakers()                          # the first check: not sending yet
        m = await app.get()
        while m["type"] != "speaker":
            m = await app.get()
        assert m["payload"] == {"airplay": False}
        await app.send("ack", {}, m["id"])
        peers.add("2601::f6aa")                                # the device chose the jukebox (over IPv6)
        await helper.check_speakers()
        m = await app.get()
        while m["type"] != "speaker":
            m = await app.get()
        assert m["payload"] == {"airplay": True}
        await app.send("ack", {}, m["id"])
        await helper.check_speakers()                          # unchanged: not sent again
        peers.clear()                                          # it chose another speaker
        await helper.check_speakers()
        m = await app.get()
        while m["type"] != "speaker":
            m = await app.get()
        assert m["payload"] == {"airplay": False}
    run(tmp_path, check)


def test_an_app_that_doesnt_ask_is_never_told(tmp_path):
    async def check(client, helper, bus):
        helper.airplay_peers = lambda: {"127.0.0.1"}
        helper.neighbours = lambda: {}
        app, _ = await pair(client, helper, bus, features=["remote"])
        await helper.check_speakers()
        seen = []
        try:
            while True:
                seen.append((await app.get(timeout=0.5))["type"])
        except asyncio.TimeoutError:
            pass
        assert "speaker" not in seen
    run(tmp_path, check)


def test_the_hello_offers_the_speaker_check(tmp_path):
    async def check(client, helper, bus):
        ws = await client.ws_connect("/helper")
        hello = json.loads((await ws.receive()).data)
        assert "speaker" in hello["payload"]["features"]
    run(tmp_path, check)
