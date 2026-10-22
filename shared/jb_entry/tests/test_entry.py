import pytest

from shared.jb_entry.entry import Entry, EntryChanged, Rejected, Selection, slot_index
from shared.selection import PRESETS, Format, clean


def changed(state, letter=None, reason=None):
    return EntryChanged(state, letter, reason, letter)


def test_slot_numbering():
    assert slot_index("A", "1") == 0
    assert slot_index("B", "7") == 16
    assert slot_index("K", "0") == 99
    assert slot_index("J", "1") == 80  # J is index 8: there is no I


def test_letter_goes_partial():
    assert Entry().key("B", 0) == [changed("partial", "B")]


def test_letter_then_digit_selects():
    e = Entry()
    e.key("B", 0)
    assert e.key("7", 1) == [Selection("B7", 16), changed("idle", reason="selected")]
    assert e.state == "idle"


def test_digit_first_is_rejected():
    e = Entry()
    assert e.key("7", 0) == [Rejected("7", "no_letter")]
    assert e.state == "idle"
    # ...and doesn't linger: the next letter + digit is a normal selection.
    e.key("B", 1)
    assert e.key("3", 2)[0] == Selection("B3", 12)


def test_digit_then_letter_does_not_select():
    e = Entry()
    e.key("7", 0)
    assert e.key("B", 1) == [changed("partial", "B")]  # B waits for its own digit


def test_second_letter_replaces_the_first():
    e = Entry()
    e.key("B", 0)
    assert e.key("C", 1) == [changed("partial", "C")]
    assert e.key("1", 2)[0] == Selection("C1", 20)


def test_cancel_clears_a_waiting_letter():
    e = Entry()
    e.key("B", 0)
    assert e.cancel(1) == [changed("idle", reason="cancelled")]


def test_timeout_clears_a_waiting_letter():
    e = Entry(timeout_ms=10000)
    e.key("B", 0.0)
    assert e.tick(9.9) == []
    assert e.tick(10.0) == [changed("idle", reason="timeout")]
    assert e.state == "idle"


def test_timeout_restarts_when_the_letter_is_replaced():
    e = Entry(timeout_ms=10000)
    e.key("B", 0.0)
    e.key("C", 8.0)
    assert e.tick(17.9) == []
    assert e.tick(18.0) == [changed("idle", reason="timeout")]


def test_idle_cancel_or_timeout_does_nothing():
    e = Entry()
    assert e.cancel(0) == []
    assert e.tick(100.0) == []


def test_every_slot_is_reachable():
    seen = set()
    for letter in "ABCDEFGHJK":
        for digit in "1234567890":
            e = Entry()
            e.key(letter, 0)
            seen.add(e.key(digit, 1)[0].index)
    assert seen == set(range(100))


def test_rejects_non_selection_keys():
    with pytest.raises(ValueError):
        Entry().key("!", 0)


def test_keys_this_jukebox_doesnt_use_are_rejected_and_change_nothing():
    e = Entry()
    assert e.key("I", 0) == [Rejected("I", "not_here")]
    e.key("B", 1)
    assert e.key("Z", 2) == [Rejected("Z", "not_here")]
    assert e.letter == "B"


def seeburg():
    return Format(clean(PRESETS["160"]))


def test_160_letters_a_to_v_and_1_to_8():
    e = Entry(fmt=seeburg())
    e.key("V", 0)
    assert e.key("8", 1)[0] == Selection("V8", 159)
    e.key("B", 2)
    assert e.key("9", 3) == [Rejected("9", "not_here")]
    assert e.letter == "B"                     # still waiting for its number
    assert e.key("0", 4) == [Rejected("0", "not_here")]
    assert e.key("1", 5)[0] == Selection("B1", 8)


def rockola():
    return Format(clean(PRESETS["rockola"]))


def test_numbers_collect_to_three_digits():
    e = Entry(fmt=rockola())
    assert e.key("2", 0) == [EntryChanged("partial", None, None, "2")]
    assert e.key("1", 1) == [EntryChanged("partial", None, None, "21")]
    assert e.key("4", 2) == [Selection("214", 114), EntryChanged("idle", None, "selected")]


def test_numbers_that_no_selection_starts_with_are_rejected():
    e = Entry(fmt=rockola())
    assert e.key("3", 0) == [Rejected("3", "no_such_number")]   # 100-299 only
    assert e.state == "idle"
    e.key("1", 1)
    assert e.key("A", 2) == [Rejected("A", "not_here")]
    assert e.typed == "1"


def test_numbers_cancel_and_time_out_like_letters():
    e = Entry(timeout_ms=10000, fmt=rockola())
    e.key("1", 0.0)
    assert e.cancel(1.0) == [EntryChanged("idle", None, "cancelled")]
    e.key("1", 2.0)
    assert e.tick(12.0) == [EntryChanged("idle", None, "timeout")]


def test_a_new_format_clears_a_half_entry():
    e = Entry()
    e.key("B", 0)
    assert e.set_format(rockola()) == [EntryChanged("idle", None, "cancelled")]
    assert e.key("1", 1)[0].typed == "1"


def test_every_numbers_slot_is_reachable_with_skipped_digits():
    fmt = Format(clean({"kind": "numbers", "places": 3, "first": 100, "last": 279,
                        "place_digits": ["12", "0123456789", "01234567"]}))
    assert fmt.page_size == 144 and fmt.codes[:3] == ["100", "101", "102"] and "108" not in fmt.codes
    seen = set()
    for code in fmt.codes:
        e = Entry(fmt=fmt)
        out = [ev for k in code for ev in e.key(k, 0)]
        seen.add(out[-2].index)
    assert seen == set(range(144))
