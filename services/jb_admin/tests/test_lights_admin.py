import base64
import json

from aiohttp import FormData

from services.jb_admin.tests.test_admin import H, run
from services.jb_lights.show import styles


def show_file(**kw):
    d = json.loads((styles.BUILTIN_DIR / "pop.json").read_text())
    d.update({"id": "my-show", "name": "My show", "for": ["rock"]})
    d.update(kw)
    return json.dumps(d).encode()


def upload(data):
    f = FormData()
    f.add_field("file", data, filename="show.json", content_type="application/json")
    return f


def test_the_light_show_page_lists_shows_and_saves_settings(tmp_path):
    async def check(c, admin):
        r = await (await c.get("/api/lights", headers=H)).json()
        ids = [s["id"] for s in r["shows"]]
        assert "arena" in ids and "starfield" in ids and len(ids) == 17 and r["settings"]["idle"] == "city"
        shows = {s["id"]: s for s in r["shows"]}
        assert {"tender", "slow"} <= set(shows["love"]["fits"]) and "love songs" in shows["love"]["tags"]
        assert {"70s", "80s"} <= set(shows["starfield"]["fits"]) and shows["starfield"]["songs"]
        metal = next(g for g in r["groups"] if g["id"] == "metal")
        assert metal["rotation"] == ["metal", "arena", "rock-show"] and not metal["custom"]
        assert {c["id"] for c in r["hardware"]["chains"]} == {"accent", "levelbar", "buttons"}
        ok = await c.post("/api/lights/settings", json={"rotations": {"metal": ["arena"]}, "brightness": 0.6,
                                                        "quiet": {"enabled": True}}, headers=H)
        s = (await ok.json())["settings"]
        assert s["rotations"] == {"metal": ["arena"]} and s["brightness"] == 0.6 and s["quiet"]["from"] == "22:00"
        assert json.loads((tmp_path / "lights" / "settings.json").read_text())["brightness"] == 0.6
        s = (await (await c.post("/api/lights/settings", json={"rotations": {"metal": None}}, headers=H)).json())["settings"]
        assert s["rotations"] == {} and s["brightness"] == 0.6               # back to every show for metal
        bad = await c.post("/api/lights/settings", json={"pin": "nope"}, headers=H)
        assert bad.status == 400 and "nope" in (await bad.json())["error"]
    run(tmp_path, check)


def test_shows_upload_download_preview_and_delete(tmp_path):
    async def check(c, admin):
        r = await c.post("/api/lights/shows", data=upload(show_file()), headers=H)
        assert r.status == 200 and (await r.json())["id"] == "my-show"
        again = await c.post("/api/lights/shows", data=upload(show_file()), headers=H)
        assert again.status == 400 and "replace" in (await again.json())["error"]
        assert (await c.post("/api/lights/shows?replace=1", data=upload(show_file()), headers=H)).status == 200
        clash = await c.post("/api/lights/shows", data=upload(show_file(id="arena")), headers=H)
        assert clash.status == 400 and "built-in" in (await clash.json())["error"]
        broken = await c.post("/api/lights/shows", data=upload(show_file(strobe=9)), headers=H)
        assert broken.status == 400 and "strobe" in (await broken.json())["error"]
        await c.post("/api/lights/settings", json={"pin": "my-show", "rotations": {"rock": ["my-show", "rock"]}}, headers=H)
        listed = await (await c.get("/api/lights", headers=H)).json()
        assert next(s for s in listed["shows"] if s["id"] == "my-show")["source"] == "uploaded"
        got = await c.get("/api/lights/shows/my-show", headers=H)
        assert (await got.json())["name"] == "My show" and "attachment" in got.headers["Content-Disposition"]
        p = await (await c.get("/api/lights/preview/my-show", headers=H)).json()
        n = sum(ch["pixels"] for ch in p["chains"])
        assert len(base64.b64decode(p["frames"])) == n * 3 * int(p["fps"] * p["seconds"])
        assert "p" in p["tiers"]                                              # the drop reaches the peak
        looks = {m["label"]: m["character"] for m in p["moments"]}
        assert looks["Verse"] == "vocal" and looks["Chorus"] == "big" and p["feel"] == "upbeat"
        love = await (await c.get("/api/lights/preview/my-show?feel=love", headers=H)).json()
        assert love["feel"] == "love" and love["plan"]["rate"] <= 0.5 and love["plan"]["accents"]["drop"] == "swell"
        assert love["tempo"] < p["tempo"] and ["love", "Love song"] in love["feels"]
        assert (await c.delete("/api/lights/shows/arena", headers=H)).status == 400
        assert (await c.delete("/api/lights/shows/my-show", headers=H)).status == 200
        s = (await (await c.get("/api/lights", headers=H)).json())["settings"]
        assert s["pin"] is None and s["rotations"] == {"rock": ["rock"]}       # nothing points at it any more
    run(tmp_path, check)


def test_learned_songs_are_listed_with_their_parts_and_can_be_forgotten(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    key = "0123456789abcdef"
    (songs / f"{key}.json").write_text(json.dumps({
        "format": 1, "song": {"title": "A song", "artist": "A band", "duration": 200.0},
        "learned": "2026-10-30T12:00:00", "plays": 3, "tempo": 120.0, "feel": {"words": ["moody", "slow"]},
        "sections": [{"start": 0.0, "end": 20.0, "label": "A", "role": "intro", "energy": 0.1, "voice": 0.0},
                     {"start": 20.0, "end": 60.0, "label": "B", "role": "verse", "energy": 0.5, "voice": 0.8},
                     {"start": 60.0, "end": 90.0, "label": "C", "role": "chorus", "energy": 1.0, "voice": 0.8,
                      "drums": 0.9}]}))
    (songs / f"{key}.npz").write_bytes(b"recording")
    (songs / "notes.json").write_text("{}")                                   # not a song's map

    async def check(c, admin):
        r = await (await c.get("/api/lights/songs", headers=H)).json()
        assert len(r["songs"]) == 1
        s = r["songs"][0]
        assert (s["title"], s["plays"], s["words"]) == ("A song", 3, ["moody", "slow"])
        assert [x["character"] for x in s["sections"]] == ["quiet", "vocal", "big"]
        assert (await c.delete("/api/lights/songs/../../etc", headers=H)).status == 404
        assert (await c.delete("/api/lights/songs/ffffffffffffffff", headers=H)).status == 404
        assert (await c.delete(f"/api/lights/songs/{key}", headers=H)).status == 200
        assert not list(songs.glob(f"{key}.*"))                              # the map and the recording
        assert (await (await c.get("/api/lights/songs", headers=H)).json())["songs"] == []
    run(tmp_path, check)
