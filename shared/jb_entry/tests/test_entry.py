import pytest

from shared.jb_entry.entry import Entry, EntryChanged, Rejected, Selection, slot_index


def changed(state, letter=None, reason=None):
    return EntryChanged(state, letter, reason)


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
        Entry().key("I", 0)
