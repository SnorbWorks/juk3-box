import asyncio

from services.jb_analyzer.tags import TagLookup, clean_tags, genre_of, mood_nudge


def test_clean_tags_drops_noise_rare_and_the_artist_name():
    raw = [{"name": "Synthwave", "count": 100}, {"name": "seen live", "count": 90},
           {"name": "Midnight Generation", "count": 60}, {"name": "80s", "count": 40},
           {"name": "synthwave", "count": 30}, {"name": "chill", "count": 12}, {"name": "obscure", "count": 3}]
    assert clean_tags(raw, "Midnight Generation") == ["synthwave", "80s", "chill"]


def test_genre_is_the_first_known_genre_in_tag_order():
    assert genre_of(["80s", "synthpop", "rock"]) == "synthpop"
    assert genre_of(["80s", "summer"]) is None


def test_mood_nudge_leans_with_the_listeners():
    assert mood_nudge(["chill", "mellow", "pop"]) < 0
    assert mood_nudge(["dance", "party"]) > 0
    assert mood_nudge(["rock", "80s"]) == 0
    assert mood_nudge(["sad", "dance"]) == 0


def test_lookup_uses_the_cache_and_needs_no_network(tmp_path):
    path = tmp_path / "tags.json"
    path.write_text('{"blue oyster cult\\ndon\'t fear the reaper": ["classic rock", "70s"]}')
    lookup = TagLookup("key", path)
    assert asyncio.run(lookup.lookup("Blue Oyster Cult", "Don't Fear The Reaper")) == ["classic rock", "70s"]
    assert asyncio.run(TagLookup(None, path).lookup("a", "b")) is None
