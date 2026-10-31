from services.jb_analyzer.tests import songmaker as sm


def starts(events):
    return [e["start"] for _, e in events if not e.get("update")]


def labels(li):
    return [s["label"] for s in li.sections.sections]


def test_live_sections_start_on_the_bar_where_the_music_changes():
    li, events, _, _ = sm.heard()
    truth = sm.section_starts(sm.PLAN)
    found = starts(events)
    assert len(found) == len(truth)
    for got, want in zip(found[1:], truth[1:]):
        assert abs(got - want) < 0.25, (got, want)             # half a beat at 120 bpm


def test_the_same_music_coming_back_gets_its_label():
    li, _, _, _ = sm.heard()
    assert labels(li) == ["A", "B", "A", "B", "C", "B"]
    assert [s["n"] for s in li.sections.sections] == [1, 1, 2, 2, 1, 3]


def test_the_loudest_music_that_comes_back_is_the_chorus():
    li, _, _, _ = sm.heard()
    roles = {s["label"]: s["role"] for s in li.sections.sections}
    assert roles["B"] == "chorus"
    assert roles["A"] != "chorus"


def test_a_section_is_known_a_bar_and_a_beat_or_so_after_it_starts():
    _, events, _, _ = sm.heard()
    for t, e in events[1:]:
        if not e.get("update"):
            assert 0 < t - e["start"] < 2.0 + 0.5 + 1.2, (t, e["start"])


def test_a_loop_going_round_is_one_section():
    li, events, _, _ = sm.heard([("A", 24)])
    assert labels(li) == ["A"]
