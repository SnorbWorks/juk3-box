import asyncio
import json
import logging

from aiohttp.test_utils import TestClient, TestServer

from services.jb_source_helper import main as helper_main, protocol, source
from services.jb_source_helper.phones import Challenge, Phones
from services.jb_source_helper.source import Helper

TOPIC = "jukebox/jb-source-helper"
SONGS = [
    {"id": "i.1", "title": "Moon River", "artist": "Andy Williams", "album": "Moon River", "duration": 164.2,
     "trackNumber": 1, "dateAdded": "2024-03-01T12:00:00Z", "artworkKey": "a" * 40},
    {"id": "i.2", "title": "Fly Me to the Moon", "artist": "Frank Sinatra", "album": "It Might as Well Be Swing",
     "duration": 148.0},
]
PLAYLISTS = [{"id": "p.1", "name": "Moons", "songIds": ["i.2", "i.1", "i.gone"]}]


class RecordingBus:
    def __init__(self):
        self.sent = []

    async def publish(self, topic, payload, retain=False):
        self.sent.append((topic, payload))

    def last(self, channel):
        return next((p for t, p in reversed(self.sent) if t == f"{TOPIC}/{channel}"), None)


class FakeWake:
    running = True

    def __init__(self):
        self.woken = 0

    async def wake(self):
        self.woken += 1

    async def ensure_running(self, wanted):
        pass

    @staticmethod
    def uuids():
        return {}


class App:
    """The phone app's side, as a test drives it."""

    def __init__(self, ws):
        self.ws = ws

    async def get(self, timeout=3):
        return json.loads((await asyncio.wait_for(self.ws.receive(), timeout)).data)

    async def send(self, type_, payload=None, id_=None):
        await self.ws.send_str(protocol.message(type_, payload, id_))


def run(tmp_path, check, wake=None):
    async def go():
        bus = RecordingBus()
        helper = Helper(bus, Phones(tmp_path / "phones.json"), tmp_path, logging.getLogger("t"),
                        "jb1", "JUKE.BOX", wake)
        await helper.start()
        client = TestClient(TestServer(helper_main.make_app(helper)))
        await client.start_server()
        try:
            await check(client, helper, bus)
        finally:
            await client.close()
    asyncio.run(go())


async def connect(client, token=None, device="dev-1"):
    app = App(await client.ws_connect("/helper"))
    hello = await app.get()
    assert hello["type"] == "hello" and hello["payload"]["jukeboxId"] == "jb1"
    await app.send("client_hello", {"deviceId": device, "deviceName": "Shawn's iPhone", "appVersion": "0.1",
                                    "token": token})
    return app


async def send_library(app, request):
    assert request["type"] == "request_library"
    await app.send("library_chunk", {"seq": 0, "total": 2, "kind": "song", "items": SONGS})
    await app.send("library_chunk", {"seq": 1, "total": 2, "kind": "playlist", "items": PLAYLISTS})
    await app.send("library_done", {"hash": "h1", "counts": {"song": 2, "playlist": 1}})
    await app.send("ack", {}, request["id"])


async def pair(client, helper, bus, device="dev-1"):
    app = await connect(client, device=device)
    challenge = await app.get()
    assert challenge["type"] == "pair_challenge"
    code = bus.last("pairing")["code"]
    assert bus.last("pairing")["device"] == "Shawn's iPhone"
    await app.send("pair_response", {"code": "x"})
    assert (await app.get()) == {"v": 1, "type": "pair_failed", "payload": {"triesLeft": 2}}
    await app.send("pair_response", {"code": code})
    paired = await app.get()
    assert paired["type"] == "paired"
    welcome = await app.get()
    assert welcome["type"] == "welcome" and welcome["payload"]["active"] is False   # until jb-core chooses it
    await choose(helper, app, device)
    return app, paired["payload"]["token"]


async def choose(helper, app, device="dev-1"):
    """jb-core picks this phone (jukebox/jb-core/source); the app hears set_active."""
    await helper.follow_choice({"active": {"kind": "helper", "id": f"helper:{device}", "name": "x"}})
    m = await app.get()
    assert m["type"] == "set_active" and m["payload"] == {"active": True}
    await app.send("ack", {}, m["id"])


def test_a_phone_pairs_with_the_code_on_the_screen_and_its_library_reaches_the_bus(tmp_path):
    async def check(client, helper, bus):
        app, token = await pair(client, helper, bus)
        assert bus.last("pairing") == {"state": "none"}
        request = await app.get()
        assert request["payload"] == {}                       # nothing known yet: everything
        assert bus.last("sync")["state"] == "syncing"
        await send_library(app, request)
        art = await app.get()                                 # covers the jukebox doesn't have
        assert art["type"] == "request_artwork" and art["payload"]["keys"] == ["a" * 40]
        lib = bus.last("library")
        assert [t["item_path"] for t in lib["tracks"]] == ["helper:dev-1/i.1", "helper:dev-1/i.2"]
        assert lib["tracks"][0]["duration_ms"] == 164200 and lib["tracks"][0]["added_at"] == 1709294400.0
        assert lib["playlists"] == [{"name": "Moons", "titles": ["Fly Me to the Moon", "Moon River"]}]
        assert bus.last("sync")["state"] == "ready"
        assert bus.last("connected")["connected"] and bus.last("connected")["name"] == "Shawn's iPhone"
        assert helper.phones.known("dev-1", token) and token not in (tmp_path / "phones.json").read_text()
    run(tmp_path, check)


def test_a_paired_phone_comes_back_with_its_token_and_an_unchanged_library_is_kept(tmp_path):
    async def check(client, helper, bus):
        app, token = await pair(client, helper, bus)
        await send_library(app, await app.get())
        await app.get()                                       # request_artwork
        await app.ws.close()
        await asyncio.sleep(0.1)
        assert not bus.last("connected")["awake"]
        app = await connect(client, token)
        welcome = await app.get()
        assert welcome["type"] == "welcome" and welcome["payload"]["active"] is True
        request = await app.get()
        assert request["payload"] == {"since": "h1"}
        await app.send("library_done", {"hash": "h1", "counts": {}})
        await app.send("ack", {}, request["id"])
        await asyncio.sleep(0.1)
        assert len(bus.last("library")["tracks"]) == 2
    run(tmp_path, check)


def test_a_pick_plays_on_the_app_and_its_progress_is_now_playing(tmp_path):
    async def check(client, helper, bus):
        app, _ = await pair(client, helper, bus)
        await send_library(app, await app.get())
        await app.get()
        pick = asyncio.create_task(helper.command({"action": "play_item", "item_path": "helper:dev-1/i.1",
                                                   "title": "Moon River"}))
        play = await app.get()
        assert play["type"] == "play" and play["payload"] == {"itemId": "i.1", "kind": "song"}
        await app.send("ack", {}, play["id"])
        await pick
        await app.send("playback_state", {"itemId": "i.1", "state": "playing", "position": 1.5, "duration": 164.2,
                                          "title": "Moon River", "artist": "Andy Williams", "album": "Moon River"})
        await asyncio.sleep(0.1)
        np = bus.last("now-playing")
        assert np["status"] == "playing" and np["position_ms"] == 1500 and np["item_path"] == "helper:dev-1/i.1"
        await app.send("track_ended", {"itemId": "i.1"})
        await asyncio.sleep(0.1)
        assert bus.last("now-playing")["ended"] and bus.last("now-playing")["status"] == "stopped"
        # A song the app can't play: jb-core hears why.
        pick = asyncio.create_task(helper.command({"action": "play_item", "item_path": "helper:dev-1/i.2"}))
        play = await app.get()
        await app.send("error", {"reason": "not_playable", "message": "not available"}, play["id"])
        await app.send("play_failed", {"itemId": "i.2", "reason": "not_playable"})   # said twice: told once
        await pick
        await asyncio.sleep(0.1)
        errors = [p for t, p in bus.sent if t == f"{TOPIC}/play-error"]
        assert errors == [{"item_path": "helper:dev-1/i.2", "title": None, "reason": "not_playable"}]
    run(tmp_path, check)


def test_a_sleeping_app_is_woken_for_a_pick(tmp_path, monkeypatch):
    wake = FakeWake()

    async def check(client, helper, bus):
        app, token = await pair(client, helper, bus)
        await send_library(app, await app.get())
        await app.get()
        await app.ws.close()
        await asyncio.sleep(0.1)
        assert bus.last("connected")["connected"]            # asleep, but it can be woken
        pick = asyncio.create_task(helper.command({"action": "play_item", "item_path": "helper:dev-1/i.1"}))
        await asyncio.sleep(0.1)
        assert wake.woken == 1
        app = await connect(client, token)                    # iOS woke it: it reconnects
        assert (await app.get())["type"] == "welcome"
        msgs = [await app.get(), await app.get()]
        play = next(m for m in msgs if m["type"] == "play")
        await app.send("ack", {}, play["id"])
        await pick
        assert not [p for t, p in bus.sent if t == f"{TOPIC}/play-error"]
    run(tmp_path, check, wake)


def test_no_app_and_no_wake_up_fails_the_pick_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "WAKE_WAIT_S", 0.2)

    async def check(client, helper, bus):
        await helper.command({"action": "play_item", "item_path": "helper:dev-9/i.1", "title": "X"})
        assert bus.last("play-error") == {"item_path": "helper:dev-9/i.1", "title": "X", "reason": "no_answer"}
    run(tmp_path, check)


def test_artwork_needs_a_paired_phones_token(tmp_path):
    async def check(client, helper, bus):
        app, token = await pair(client, helper, bus)
        key = "b" * 40
        jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
        r = await client.post(f"/artwork/{key}", data=jpeg, headers={"Authorization": "Bearer nope"})
        assert r.status == 401
        r = await client.post("/artwork/../../etc", data=jpeg, headers={"Authorization": f"Bearer {token}"})
        assert r.status in (400, 404)
        r = await client.post(f"/artwork/{key}", data=b"GIF89a", headers={"Authorization": f"Bearer {token}"})
        assert r.status == 400
        r = await client.post(f"/artwork/{key}", data=jpeg, headers={"Authorization": f"Bearer {token}"})
        assert r.status == 204 and (tmp_path / "artwork" / f"{key}.jpg").read_bytes() == jpeg

        async def slowly(body):                               # arriving in pieces, as over Wi-Fi
            for i in range(0, len(body), 16384):
                yield body[i:i + 16384]
                await asyncio.sleep(0.01)
        big = b"\xff\xd8" + bytes(range(256)) * 1200 + b"\xff\xd9"   # ~300 KB
        r = await client.post(f"/artwork/{key}", data=slowly(big), headers={"Authorization": f"Bearer {token}"})
        assert r.status == 204 and (tmp_path / "artwork" / f"{key}.jpg").read_bytes() == big
        # Told once the batch stops, so the screen loads covers it drew too early.
        assert bus.last("artwork") is None
        await asyncio.sleep(source.ARTWORK_NOTE_S + 0.2)
        assert [t for t, _ in bus.sent].count(f"{TOPIC}/artwork") == 1
    run(tmp_path, check)


def test_a_pairing_code_runs_out_of_tries_and_time():
    c = Challenge("d", "n", now=0)
    first = c.code
    assert not c.check("nope", now=1) and c.tries == 2
    assert not c.check(first, now=10_000)                     # expired
    assert c.check(first, now=1)
    c.renew(now=0)
    assert c.tries == 3


def test_forgetting_a_phone_removes_it_and_tells_jb_library(tmp_path):
    async def check(client, helper, bus):
        app, token = await pair(client, helper, bus)
        await send_library(app, await app.get())
        await app.get()
        await helper.command({"action": "forget", "device_id": "dev-1"})
        assert not helper.phones.phones and not (tmp_path / "library-dev-1.json").exists()
        assert bus.last("forgotten") == {"mac": "helper:dev-1"}
        assert bus.last("library")["tracks"] == [] and not bus.last("connected")["connected"]
    run(tmp_path, check)


def test_the_cabinet_stops_asking_after_a_few_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "PAIR_ROUNDS", 2)

    async def check(client, helper, bus):
        app = await connect(client)
        assert (await app.get())["type"] == "pair_challenge"
        for _ in range(3):
            await app.send("pair_response", {"code": "wrong"})
            await app.get()                                   # pair_failed
        assert (await app.get())["type"] == "pair_challenge"  # a second code
        for _ in range(3):
            await app.send("pair_response", {"code": "wrong"})
        msgs = [await app.get() for _ in range(3)]
        assert msgs[-1] == {"v": 1, "type": "pair_failed", "payload": {"triesLeft": 0}}
        closed = await app.ws.receive()
        assert closed.type.name in ("CLOSE", "CLOSED", "CLOSING")
        await asyncio.sleep(0.1)
        assert bus.last("pairing") == {"state": "none"} and not helper.phones.phones
    run(tmp_path, check)


def test_pairing_turns_off_once_a_phone_pairs_and_a_stranger_is_told(tmp_path):
    async def check(client, helper, bus):
        assert helper.settings()["allow_pairing"]             # nothing paired yet
        await pair(client, helper, bus)
        assert not helper.settings()["allow_pairing"]
        stranger = await connect(client, device="dev-2")
        closed = await stranger.get()
        assert closed["type"] == "pair_closed" and "Sources > App" in closed["payload"]["message"]
        assert bus.last("pairing") == {"state": "none"}       # nothing on the cabinet
        await helper.command({"action": "settings", "allow_pairing": True})
        assert bus.last("settings")["allow_pairing"]
        again = await connect(client, device="dev-2")
        assert (await again.get())["type"] == "pair_challenge"
    run(tmp_path, check)


def test_a_second_phone_waits_for_the_cabinet_and_takes_over_when_chosen(tmp_path):
    async def check(client, helper, bus):
        first, _ = await pair(client, helper, bus)
        await send_library(first, await first.get())
        await first.get()                                     # request_artwork
        await helper.command({"action": "settings", "allow_pairing": True})
        second = await connect(client, device="dev-2")
        assert (await second.get())["type"] == "pair_challenge"
        await second.send("pair_response", {"code": bus.last("pairing")["code"]})
        assert (await second.get())["type"] == "paired"
        assert (await second.get())["payload"]["active"] is False
        await asyncio.sleep(0.1)
        listing = {p["device_id"]: p for p in bus.last("connected")["phones"]}
        assert listing["dev-2"]["reachable"] and listing["dev-1"]["songs"] == 2
        assert bus.last("connected")["device_id"] == "dev-1"  # still the first one's
        await helper.follow_choice({"active": {"kind": "helper", "id": "helper:dev-2"}})
        msgs = {(await first.get())["type"], }
        assert msgs == {"set_active"}
        m = await second.get()
        assert m["type"] == "set_active" and m["payload"] == {"active": True}
        assert bus.last("connected")["device_id"] == "dev-2"
        # A Bluetooth phone chosen: no app phone is the source.
        await helper.follow_choice({"active": {"kind": "phone", "id": "AA:BB:CC:DD:EE:FF"}})
        assert bus.last("connected")["device_id"] is None and bus.last("library")["tracks"] == []
    run(tmp_path, check)


def test_the_owner_renames_a_phone_and_the_jukebox(tmp_path):
    async def check(client, helper, bus):
        renamed = []

        async def on_rename(name):
            renamed.append(name)
        helper.on_rename = on_rename
        app, token = await pair(client, helper, bus)
        await helper.command({"action": "rename", "device_id": "dev-1", "name": "  Shawn's  phone "})
        assert bus.last("connected")["name"] == "Shawn's phone"
        helper.phones.seen("dev-1", "iPhone")                 # the app's own name doesn't win
        assert helper.phones.name_of("dev-1") == "Shawn's phone"
        await helper.command({"action": "settings", "name": 'The "Bar" $Box', "ble": False})
        assert renamed == ["The Bar Box"] and bus.last("settings")["name"] == "The Bar Box"
        assert (tmp_path / "airplay.env").read_text() == 'JB_AIRPLAY_NAME="The Bar Box"\n'
        assert not bus.last("settings")["ble"]
    run(tmp_path, check)
