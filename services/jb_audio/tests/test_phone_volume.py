import asyncio
import logging
import json

from services.jb_audio import main, phone_volume as pv
from services.jb_audio.outputs import Settings

MAC = "88:51:F2:56:4B:2B"
TREE = """/org/bluez
/org/bluez/hci0
/org/bluez/hci0/dev_88_51_F2_56_4B_2B
/org/bluez/hci0/dev_88_51_F2_56_4B_2B/fd1
/org/bluez/hci0/dev_88_51_F2_56_4B_2B/player0
"""


def stream(id_, volume, name="bluez_input.88_51_F2_56_4B_2B.2"):
    return {"id": id_, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": name, "media.class": "Stream/Output/Audio"},
                     "params": {"Props": [{"volume": volume, "channelVolumes": [1.0, 1.0]}]}}}


def test_the_knob_and_the_phone_share_one_scale():
    # Measured: the phone at 119/127 made the stream's master volume 0.823.
    assert pv.avrcp(1.0) == 127 and pv.avrcp(0.2) == 25 and pv.avrcp(-1) == 0 and pv.avrcp(2) == 127
    assert pv.level_of(0.823) == round(119 / 127, 3)
    assert pv.avrcp(pv.level_of((32 / 127) ** 3)) == 32


def test_the_phones_audio_link_is_found_by_its_address():
    assert pv.transport_path(TREE, MAC) == "/org/bluez/hci0/dev_88_51_F2_56_4B_2B/fd1"
    assert pv.transport_path(TREE, "11:22:33:44:55:66") is None
    # after a reconnect (seen Oct 26)
    assert pv.transport_path("/org/bluez/hci0/dev_88_51_F2_56_4B_2B/sep1/fd3\n", MAC) == \
        "/org/bluez/hci0/dev_88_51_F2_56_4B_2B/sep1/fd3"


def test_a_restart_of_jb_audio_isnt_a_new_connection():
    assert pv.is_new_connection(MAC, "boot1", None)
    assert not pv.is_new_connection(MAC, "boot1", {"mac": MAC, "boot": "boot1"})
    assert pv.is_new_connection(MAC, "boot2", {"mac": MAC, "boot": "boot1"})       # the jukebox restarted
    assert pv.is_new_connection("11:22:33:44:55:66", "boot1", {"mac": MAC, "boot": "boot1"})


def test_phone_stream_volumes_from_pw_dump_batches():
    names = {}
    assert pv.stream_volumes([stream(90, 0.823), stream(76, 1.0, "juk3box AirPlay")], names) == [(90, 0.823)]
    # a later update may leave out the props: the name is remembered
    update = {"id": 90, "type": "PipeWire:Interface:Node",
              "info": {"params": {"Props": [{"volume": 0.5, "channelVolumes": [1.0, 1.0]}]}}}
    assert pv.stream_volumes([update], names) == [(90, 0.5)]
    assert pv.stream_volumes([{"id": 90, "info": None}], names) == [] and 90 not in names


def test_pw_dump_monitor_output_splits_into_batches():
    text = json.dumps([stream(90, 1.0)]) + "\n" + json.dumps([{"id": 96, "info": None}]) + "\n[\n {\"id\""
    batches, rest = pv.json_batches(text)
    assert len(batches) == 2 and rest.startswith("[")


def run_phone(monkeypatch, master, level=None, sent=(), unsent=False):
    calls, levels = [], []

    async def fake_run(*args):
        calls.append(args)
        if args[:2] == ("busctl", "tree"):
            return TREE
        if args[:2] == ("busctl", "get-property"):
            return "q 0"
        return ""

    async def set_level(v):
        levels.append(v)

    monkeypatch.setattr(pv, "_run", fake_run)
    phone = pv.PhoneVolume(set_level)
    phone.attach(MAC)
    phone.level, phone._unsent = level, unsent
    phone._sent = [(pv.time.monotonic(), n) for n in sent]
    asyncio.run(phone._stream_changed(90, master))
    return calls, levels


def test_the_phones_slider_moves_the_knob_and_is_applied_once(monkeypatch):
    calls, levels = run_phone(monkeypatch, (64 / 127) ** 3, level=0.2)
    assert ("pw-cli", "set-param", "90", "Props", "{ volume: 1.0 }") in calls     # not stacked on the DAC
    assert levels == [round(64 / 127, 3)]


def test_our_own_echo_doesnt_move_the_knob(monkeypatch):
    _, levels = run_phone(monkeypatch, (25 / 127) ** 3, level=0.4, sent=[25])
    assert levels == []
    _, levels = run_phone(monkeypatch, (25 / 127) ** 3, level=0.2)                # no change
    assert levels == []


def test_just_connected_the_knobs_level_goes_to_the_phone(monkeypatch):
    # The stream comes up at the phone's old level; 20% wins.
    calls, levels = run_phone(monkeypatch, (100 / 127) ** 3, level=0.2, unsent=True)
    assert levels == []
    assert ("busctl", "set-property", "org.bluez", "/org/bluez/hci0/dev_88_51_F2_56_4B_2B/fd1",
            "org.bluez.MediaTransport1", "Volume", "q", "25") in calls


def test_a_phone_connecting_starts_everything_at_20_percent(monkeypatch, tmp_path):
    levels, follows, saved = [], [], []

    async def fake_set_level(bus, log, outputs, level):
        levels.append(level)

    async def fake_state():
        return {"volume": 0.6, "muted": False}

    class Phone:
        def attach(self, mac): self.mac = mac
        def detach(self): self.mac = None
        async def follow(self, level): follows.append(level)

    class Outputs:
        settings = Settings()

    monkeypatch.setattr(main, "set_level", fake_set_level)
    monkeypatch.setattr(main, "get_state", fake_state)
    monkeypatch.setattr(main, "boot_id", lambda: "boot1")
    monkeypatch.setattr(Settings, "save", lambda self, path=None: saved.append(dict(self.phone or {})))
    out, phone = Outputs(), Phone()
    log = logging.getLogger("test")

    asyncio.run(main.phone_connected(None, log, out, phone, {"connected": True, "mac": MAC}))
    assert levels == [0.2] and follows == [0.2] and out.settings.phone == {"mac": MAC, "boot": "boot1"}
    # jb-audio restarting hears the retained message again: no reset, the slider just follows the knob
    asyncio.run(main.phone_connected(None, log, out, phone, {"connected": True, "mac": MAC}))
    assert levels == [0.2] and follows == [0.2, 0.6]
    # leaving, then coming back: a new connection
    asyncio.run(main.phone_connected(None, log, out, phone, {"connected": False}))
    assert phone.mac is None and out.settings.phone is None
    asyncio.run(main.phone_connected(None, log, out, phone, {"connected": True, "mac": MAC}))
    assert levels == [0.2, 0.2]
