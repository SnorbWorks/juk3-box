from services.jb_analyzer.feel import era_of, feel


def test_a_slow_love_song():
    f = feel(["love songs", "ballad", "soul"], "R&B", 1987, {"tempo": 68, "drums": 0.3},
             {"moods": {"tender": 0.02, "sad": 0.01, "happy": 0.005}}, {"major": 1, "sure": 0.8}, 0.8)
    assert f["tender"] >= 0.6 and f["pace"] < 0.3 and f["energy"] < 0.4
    assert {"love song", "slow", "vocals", "80s"} <= set(f["words"])


def test_a_moody_one_in_a_minor_key():
    f = feel(["trip-hop", "downtempo"], None, None, {"tempo": 90, "drums": 0.5}, None, {"major": -1, "sure": 0.9})
    assert f["moody"] >= 0.6 and f["valence"] < 0.5 and "moody" in f["words"]


def test_a_hard_fast_one():
    f = feel(["thrash metal"], "Metal", None, {"tempo": 180, "drums": 0.95, "brightness": 0.8},
             {"moods": {"angry": 0.03, "exciting": 0.02}})
    assert f["aggressive"] >= 0.6 and f["energy"] >= 0.7 and f["pace"] >= 0.75
    assert "hard" in f["words"] and "fast" in f["words"]


def test_knowing_nothing_is_middle_of_the_road():
    f = feel()
    assert all(0.0 <= f[k] <= 1.0 for k in ("energy", "valence", "tender", "aggressive", "groove", "moody", "pace"))
    assert f["era"] is None and "mid-tempo" in f["words"]


def test_the_decade_from_the_year_or_the_tags():
    assert era_of(1975) == "70s" and era_of(2004) == "00s"
    assert era_of(None, ["synthpop", "80s"]) == "80s" and era_of(None, ["1990s"]) == "90s"
    assert era_of(None, ["2010s"]) == "10s" and era_of(None, ["indie"]) is None
