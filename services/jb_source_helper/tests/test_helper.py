import asyncio
import hashlib
import json
import logging

from aiohttp.test_utils import TestClient, TestServer

from services.jb_source_helper import catalog, main as helper_main, protocol, source
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


def run(tmp_path, check, wake=None, fetch=None):
    async def go():
        bus = RecordingBus()
        helper = Helper(bus, Phones(tmp_path / "phones.json"), tmp_path, logging.getLogger("t"),
                        "jb1", "Juk3box", wake, **({"fetch": fetch} if fetch else {}))
        await helper.start()
        client = TestClient(TestServer(helper_main.make_app(helper)))
        await client.start_server()
        try:
            await check(client, helper, bus)
        finally:
            await client.close()
    asyncio.run(go())


async def connect(client, token=None, device="dev-1", features=None):
    app = App(await client.ws_connect("/helper"))
    hello = await app.get()
    assert hello["type"] == "hello" and hello["payload"]["jukeboxId"] == "jb1"
    await app.send("client_hello", {"deviceId": device, "deviceName": "Alex's iPhone", "appVersion": "0.1",
                                    "token": token, **({"features": features} if features else {})})
    return app


async def send_library(app, request):
    assert request["type"] == "request_library"
    await app.send("library_chunk", {"seq": 0, "total": 2, "kind": "song", "items": SONGS})
    await app.send("library_chunk", {"seq": 1, "total": 2, "kind": "playlist", "items": PLAYLISTS})
    await app.send("library_done", {"hash": "h1", "counts": {"song": 2, "playlist": 1}})
    await app.send("ack", {}, request["id"])


async def pair(client, helper, bus, device="dev-1", features=None):
    app = await connect(client, device=device, features=features)
    challenge = await app.get()
    assert challenge["type"] == "pair_challenge"
    code = bus.last("pairing")["code"]
    assert bus.last("pairing")["device"] == "Alex's iPhone"
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
        assert bus.last("connected")["connected"] and bus.last("connected")["name"] == "Alex's iPhone"
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
        await helper.command({"action": "rename", "device_id": "dev-1", "name": "  Alex's  phone "})
        assert bus.last("connected")["name"] == "Alex's phone"
        helper.phones.seen("dev-1", "iPhone")                 # the app's own name doesn't win
        assert helper.phones.name_of("dev-1") == "Alex's phone"
        await helper.command({"action": "settings", "name": 'The "Bar" $Box', "ble": False})
        assert renamed == ["The Bar Box"] and bus.last("settings")["name"] == "The Bar Box"
        assert (tmp_path / "airplay.env").read_text() == 'JB_AIRPLAY_NAME="The Bar Box"\n'
        assert not bus.last("settings")["ble"]
    run(tmp_path, check)


# Apple Music (docs/contracts/apple-music.md) ---------------------------------

ART = "https://is1-ssl.mzstatic.com/image/thumb/{}/600x600bb.jpg"
GENRES = {"storefront": "us", "genres": [
    {"id": "21", "name": "Rock", "playlist": {"id": "pl.rock", "name": "Rock Hits", "curator": "Apple Music Rock",
                                             "artworkUrl": ART.format("rock")}},
    {"id": "14", "name": "Pop", "playlist": {"id": "pl.pop", "name": "Pop Hits", "curator": "Apple Music Pop",
                                            "artworkUrl": ART.format("pop")}},
    {"id": "20", "name": "Alternative", "playlist": None},
]}
CHARTS = {
    "playlists": [{"id": "pl.rock", "name": "Rock Hits", "curator": "Apple Music Rock", "artworkUrl": ART.format("rock")}],
    "albums": [{"id": "a1", "name": "The Resistance", "artist": "Muse", "explicit": False, "artworkUrl": ART.format("a1")},
               {"id": "a2", "name": "Rude", "artist": "X", "explicit": True, "artworkUrl": ART.format("a2")}],
    "songs": [{"id": "1440833098", "title": "Uprising", "artist": "Muse", "album": "The Resistance",
               "genre": "Alternative", "duration": 304.8, "explicit": False, "artworkUrl": ART.format("a1")},
              {"id": "s2", "title": "Rude Song", "artist": "X", "album": "Rude", "explicit": True,
               "artworkUrl": "http://evil.example.com/x.jpg"}],
}


class FakeFetch:
    """Apple's image servers: a small JPEG per link."""

    def __init__(self):
        self.urls = []

    async def __call__(self, url):
        self.urls.append(url)
        if "huge" in url:
            return b"\xff\xd8" + b"0" * (3 * 1024 * 1024)
        return b"\xff\xd8" + url.encode()


async def nothing_from(app, wait=0.2):
    try:
        m = await asyncio.wait_for(app.ws.receive(), wait)
    except asyncio.TimeoutError:
        return True
    raise AssertionError(f"the app was sent {m.data}")


async def catalog_phone(client, helper, bus, features=("catalog",), subscribed=True):
    """A paired app phone that's the source, its library read."""
    app, _ = await pair(client, helper, bus, features=list(features) if features else None)
    await send_library(app, await app.get())
    await app.get()                                           # request_artwork
    await app.send("music_status", {"authorization": "authorized", "canPlay": True, "canPlayCatalog": subscribed})
    await asyncio.sleep(0.05)
    return app


async def answer(helper, app, cmd, type_, result):
    """jb-core's catalog cmd; the app answers the command it's sent."""
    task = asyncio.create_task(helper.command({"action": "catalog", **cmd}))
    m = await app.get()
    assert m["type"] == type_
    await app.send("ack", result, m["id"])
    await task
    return m


def cover_of(url):
    return f"/covers/{hashlib.sha1(url.encode()).hexdigest()}.jpg"


def test_apple_music_is_off_until_the_owner_turns_it_on_and_needs_a_subscribed_new_app(tmp_path):
    async def check(client, helper, bus):
        assert bus.last("catalog/status") == {"v": 1, "on": False, "available": False, "reason": "off", "storefront": None}
        assert bus.last("settings")["apple_music"] == {"on": False, "clean": False, "genres": None, "refresh_hours": 24}
        await helper.command({"action": "settings", "apple_music": {"on": True, "refresh_hours": 5}})
        assert bus.last("settings")["apple_music"]["on"] and bus.last("settings")["apple_music"]["refresh_hours"] == 24
        assert bus.last("catalog/status")["reason"] == "no_phone"
        app = await catalog_phone(client, helper, bus, features=None)          # the App Store 1.0 app
        assert bus.last("catalog/status")["reason"] == "app_update"
        await helper.command({"action": "catalog", "what": "genres"})
        assert await nothing_from(app)                                         # never sent catalog commands
        assert bus.last("catalog/genres") == {"v": 1, "at": None, "state": "error", "reason": "app_update"}
        # A catalog pick on it fails without reaching the app.
        await helper.command({"action": "play_item", "item_path": "helper:dev-1/catalog:1", "title": "X"})
        assert bus.last("play-error")["reason"] == "not_playable"
        assert await nothing_from(app)
    run(tmp_path, check)


def test_no_subscription_says_so(tmp_path):
    async def check(client, helper, bus):
        await helper.command({"action": "settings", "apple_music": {"on": True}})
        await catalog_phone(client, helper, bus, subscribed=False)
        assert bus.last("catalog/status") == {"v": 1, "on": True, "available": False, "reason": "no_subscription",
                                              "storefront": None}
    run(tmp_path, check)


def test_apple_music_lists_come_from_the_app_with_their_covers_and_are_kept(tmp_path):
    fetch = FakeFetch()

    async def check(client, helper, bus):
        await helper.command({"action": "settings", "apple_music": {"on": True}})
        app = await catalog_phone(client, helper, bus)
        assert bus.last("catalog/status")["available"]
        await answer(helper, app, {"what": "genres"}, "catalog_genres", GENRES)
        states = [p["state"] for t, p in bus.sent if t == f"{TOPIC}/catalog/genres"]
        assert states == ["loading", "ready"]
        g = bus.last("catalog/genres")
        assert [x["name"] for x in g["genres"]] == ["Rock", "Pop", "Alternative"]
        assert g["genres"][0]["cover"] == cover_of(ART.format("rock")) and g["genres"][2]["cover"] is None
        assert g["genres"][0]["playlist"] == {"id": "pl.rock", "name": "Rock Hits", "curator": "Apple Music Rock",
                                              "cover": cover_of(ART.format("rock"))}
        assert (tmp_path / "artwork" / cover_of(ART.format("rock"))[len("/covers/"):]).read_bytes().startswith(b"\xff\xd8")
        assert bus.last("catalog/status")["storefront"] == "us"
        # Asked again: the kept copy, at once, without the phone.
        await helper.command({"action": "catalog", "what": "genres"})
        assert await nothing_from(app) and bus.last("catalog/genres")["state"] == "ready"
        # The owner's genres, in the owner's order (unknown ids skipped); every genre stays in "all".
        await helper.command({"action": "settings", "apple_music": {"genres": ["14", "99", "21"]}})
        g = bus.last("catalog/genres")
        assert [x["id"] for x in g["genres"]] == ["14", "21"] and [x["id"] for x in g["all"]] == ["21", "14", "20"]

        m = await answer(helper, app, {"what": "charts", "genre": "21"}, "catalog_charts", CHARTS)
        assert m["payload"] == {"genre": "21", "limit": 25}
        c = bus.last("catalog/charts/21")
        assert c["state"] == "ready" and c["genre"] == {"id": "21", "name": "Rock"}
        assert [a["id"] for a in c["albums"]] == ["a1", "a2"]
        assert c["songs"][0] == {"title": "Uprising", "artist": "Muse", "album": "The Resistance", "genre": "Alternative",
                                 "artwork_url": cover_of(ART.format("a1")), "item_path": "helper:dev-1/catalog:1440833098",
                                 "duration_ms": 304800, "explicit": False, "catalog": True}
        assert c["songs"][1]["artwork_url"] is None                           # not Apple's image servers
        assert not [u for u in fetch.urls if "evil" in u]
        # Clean versions only: explicit albums and songs go, from every list.
        await helper.command({"action": "settings", "apple_music": {"clean": True}})
        c = bus.last("catalog/charts/21")
        assert [a["id"] for a in c["albums"]] == ["a1"] and [s["title"] for s in c["songs"]] == ["Uprising"]

        m = await answer(helper, app, {"what": "items", "kind": "playlist", "id": "pl.rock"}, "catalog_items",
                         {"id": "pl.rock", "name": "Rock Hits", "artist": None, "artworkUrl": ART.format("rock"),
                          "songs": CHARTS["songs"]})
        assert m["payload"] == {"kind": "playlist", "id": "pl.rock"}
        items = bus.last("catalog/items/playlist/pl.rock")
        assert items["name"] == "Rock Hits" and items["cover"] == cover_of(ART.format("rock"))
        assert [s["title"] for s in items["songs"]] == ["Uprising"]
        # Each list on disk, one file per topic.
        assert {p.name for p in (tmp_path / "catalog").glob("*.json")} >= {
            "genres.json", "charts-21.json", "items-playlist-pl.rock.json"}
        # Bad requests are ignored.
        await helper.command({"action": "catalog", "what": "items", "kind": "song", "id": "x"})
        await helper.command({"action": "catalog", "what": "charts", "genre": "a/#"})
        assert await nothing_from(app)
    run(tmp_path, check, fetch=fetch)


def test_a_catalog_song_plays_with_its_cover_on_now_playing(tmp_path):
    async def check(client, helper, bus):
        await helper.command({"action": "settings", "apple_music": {"on": True}})
        app = await catalog_phone(client, helper, bus)
        await answer(helper, app, {"what": "charts"}, "catalog_charts", CHARTS)
        assert bus.last("catalog/charts/all")["genre"] is None
        # A track another phone listed plays on the phone that has the jukebox.
        pick = asyncio.create_task(helper.command({"action": "play_item", "item_path": "helper:dev-0/catalog:1440833098",
                                                   "title": "Uprising"}))
        play = await app.get()
        assert play["type"] == "play" and play["payload"] == {"itemId": "catalog:1440833098", "kind": "song"}
        await app.send("ack", {}, play["id"])
        await pick
        await app.send("playback_state", {"itemId": "catalog:1440833098", "state": "playing", "position": 3,
                                          "duration": 304.8, "title": "Uprising", "artist": "Muse", "album": None})
        await asyncio.sleep(0.1)
        np = bus.last("now-playing")
        assert np["item_path"] == "helper:dev-1/catalog:1440833098" and np["catalog"] is True
        assert np["artwork_url"] == cover_of(ART.format("a1")) and np["album"] == "The Resistance"
    run(tmp_path, check, fetch=FakeFetch())


def test_a_refresh_that_fails_keeps_the_old_lists_and_old_covers_go_when_no_list_names_them(tmp_path):
    async def check(client, helper, bus):
        await helper.command({"action": "settings", "apple_music": {"on": True}})
        app = await catalog_phone(client, helper, bus)
        (tmp_path / "artwork").mkdir(exist_ok=True)
        mine = tmp_path / "artwork" / ("c" * 40 + ".jpg")                    # an app upload: not ours to delete
        mine.write_bytes(b"\xff\xd8")
        await answer(helper, app, {"what": "charts"}, "catalog_charts", CHARTS)
        old = tmp_path / "artwork" / (cover_of(ART.format("a2"))[len("/covers/"):])
        assert old.exists()
        # Refresh: the app is offline.
        task = asyncio.create_task(helper.command({"action": "catalog", "what": "charts", "refresh": True}))
        m = await app.get()
        assert bus.last("catalog/charts/all")["state"] == "loading" and len(bus.last("catalog/charts/all")["albums"]) == 2
        await app.send("error", {"reason": "offline", "message": "no internet"}, m["id"])
        await task
        c = bus.last("catalog/charts/all")
        assert c["state"] == "error" and c["reason"] == "offline" and len(c["albums"]) == 2
        # Again, with a2 gone from the chart and a huge cover for a new album.
        charts = {**CHARTS, "albums": [CHARTS["albums"][0], {"id": "a3", "name": "Big", "artist": "Y",
                                                             "artworkUrl": ART.format("huge")}]}
        await answer(helper, app, {"what": "charts", "refresh": True}, "catalog_charts", charts)
        c = bus.last("catalog/charts/all")
        assert c["state"] == "ready" and c["albums"][1]["cover"] is None     # over 2 MB: none
        await asyncio.sleep(0.1)
        assert not old.exists() and mine.exists()
        assert (tmp_path / "artwork" / (cover_of(ART.format("a1"))[len("/covers/"):])).exists()
    run(tmp_path, check, fetch=FakeFetch())


def test_kept_lists_run_out_after_refresh_hours(tmp_path, monkeypatch):
    async def check(client, helper, bus):
        await helper.command({"action": "settings", "apple_music": {"on": True, "refresh_hours": 6}})
        app = await catalog_phone(client, helper, bus)
        await answer(helper, app, {"what": "genres"}, "catalog_genres", GENRES)
        later = source.time.time() + 7 * 3600
        monkeypatch.setattr(catalog.time, "time", lambda: later)
        await answer(helper, app, {"what": "genres"}, "catalog_genres", GENRES)   # fetched again
    run(tmp_path, check, fetch=FakeFetch())


def test_cover_links_must_be_apples():
    assert catalog.cover_allowed("https://is1-ssl.mzstatic.com/a.jpg")
    assert not catalog.cover_allowed("http://is1-ssl.mzstatic.com/a.jpg")
    assert not catalog.cover_allowed("https://mzstatic.com.evil.net/a.jpg")
    assert not catalog.cover_allowed("https://evilmzstatic.com/a.jpg")
    assert not catalog.cover_allowed(None)


def test_refresh_now_fetches_the_apple_music_screens_lists_and_marks_the_rest_stale(tmp_path):
    async def check(client, helper, bus):
        await helper.command({"action": "settings", "apple_music": {"on": True}})
        app = await catalog_phone(client, helper, bus)
        await answer(helper, app, {"what": "charts", "genre": "21"}, "catalog_charts", CHARTS)
        task = asyncio.create_task(helper.command({"action": "catalog", "what": "all", "refresh": True}))
        asked = {}
        for _ in range(2):
            m = await app.get()
            asked[m["type"]] = m
        assert set(asked) == {"catalog_genres", "catalog_charts"} and asked["catalog_charts"]["payload"]["genre"] is None
        await app.send("ack", GENRES, asked["catalog_genres"]["id"])
        await app.send("ack", CHARTS, asked["catalog_charts"]["id"])
        await task
        assert bus.last("catalog/genres")["state"] == "ready" and bus.last("catalog/charts/all")["state"] == "ready"
        await answer(helper, app, {"what": "charts", "genre": "21"}, "catalog_charts", CHARTS)   # stale: fetched again
    run(tmp_path, check, fetch=FakeFetch())


def test_the_remote_app_hears_up_next_and_its_skip_reaches_jb_core(tmp_path):
    async def check(client, helper, bus):
        app, _ = await pair(client, helper, bus, features=["catalog", "remote"])
        m = await app.get()                                   # sent when chosen: nothing queued yet
        assert m["type"] == "up_next" and m["payload"] == {}
        await app.send("ack", {}, m["id"])
        await send_library(app, await app.get())
        await app.get()                                       # request_artwork
        queue = {"shuffle_active": False, "upcoming": [{"title": "Uprising", "artist": "Muse", "album": "The Resistance",
                                                        "item_path": "helper:dev-1/i.9"}]}
        await helper.follow_queue(queue)
        m = await app.get()
        assert m["type"] == "up_next"
        assert m["payload"] == {"title": "Uprising", "artist": "Muse", "album": "The Resistance"}
        await app.send("ack", {}, m["id"])
        await helper.follow_queue(queue)                      # unchanged: not sent again
        await app.send("playback_state", {"itemId": "i.1", "state": "playing", "position": 1, "duration": 164})
        await app.send("skip", {"itemId": "i.2"})             # a late tap for another song: ignored
        await app.send("skip", {"itemId": "i.1"})
        await asyncio.sleep(0.1)
        skips = [p for t, p in bus.sent if t == "jukebox/jb-core/cmd"]
        assert skips == [{"action": "skip", "by": "phone"}]
    run(tmp_path, check)


def test_the_hello_says_the_jukebox_takes_the_remote(tmp_path):
    async def check(client, helper, bus):
        app = App(await client.ws_connect("/helper"))
        hello = await app.get()
        assert "remote" in hello["payload"]["features"]
    run(tmp_path, check)
