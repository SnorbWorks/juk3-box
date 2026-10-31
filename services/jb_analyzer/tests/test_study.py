from services.jb_analyzer import study
from services.jb_analyzer.tests import songmaker as sm


def test_the_study_finds_the_sections_their_labels_and_the_chorus():
    _, _, rec, _ = sm.heard()
    m = study.study(rec)
    assert abs(m["tempo"] - 120) < 1
    starts = [s["start"] for s in m["sections"]]
    for got, want in zip(starts, sm.section_starts(sm.PLAN)):
        assert abs(got - want) < 0.1, (starts, sm.section_starts(sm.PLAN))
    assert [s["label"] for s in m["sections"]] == ["A", "B", "A", "B", "C", "B"]
    assert {s["role"] for s in m["sections"] if s["label"] == "B"} == {"chorus"}
    assert m["sections"][0]["role"] == "intro"
    assert sum(1 for s in m["sections"] if s.get("peak")) == 1


def test_the_map_says_what_kind_of_song_it_is():
    _, _, rec, _ = sm.heard()
    m = study.study(rec)
    f = m["feel"]
    assert set(f) >= {"energy", "valence", "tender", "aggressive", "groove", "moody", "pace", "voice", "era", "words"}
    assert "instrumental" in f["words"]                        # no ears in the tests: nobody singing
    assert m["format"] == 1 and m["plays"] == 1 and m["learned"]
