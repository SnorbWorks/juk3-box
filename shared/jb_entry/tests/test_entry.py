import pytest

from shared.jb_entry.entry import Entry, EntryChanged, Selection, slot_index


def changed(state, letter=None, digit=None, reason=None):
    return EntryChanged(state, letter, digit, reason)


def test_slot_numbering():
    assert slot_index("A", "1") == 0
    assert slot_index("B", "7") == 16
    assert slot_index("K", "0") == 99
    assert slot_index("J", "1") == 80  # J is index 8: there is no I


# One test per row of the brief's transition table.

def test_idle_letter_goes_partial():
    assert Entry().key("B", 0) == [changed("partial", letter="B")]


def test_idle_digit_goes_partial():
    assert Entry().key("7", 0) == [changed("partial", digit="7")]


def test_partial_letter_then_letter_replaces_it():
    e = Entry()
    e.key("B", 0)
    assert e.key("C", 1) == [changed("partial", letter="C")]


def test_partial_letter_then_digit_selects():
    e = Entry()
    e.key("B", 0)
    assert e.key("7", 1) == [Selection("B7", 16), changed("idle", reason="selected")]
    assert e.state == "idle"


def test_partial_digit_then_digit_replaces_it():
    e = Entry()
    e.key("7", 0)
    assert e.key("8", 1) == [changed("partial", digit="8")]


def test_partial_digit_then_letter_selects():
    e = Entry()
    e.key("7", 0)
    assert e.key("B", 1) == [Selection("B7", 16), changed("idle", reason="selected")]


def test_partial_cancel_goes_idle():
    e = Entry()
    e.key("B", 0)
    assert e.cancel(1) == [changed("idle", reason="cancelled")]


def test_partial_timeout_goes_idle():
    e = Entry(timeout_ms=10000)
    e.key("B", 0.0)
    assert e.tick(9.9) == []
    assert e.tick(10.0) == [changed("idle", reason="timeout")]
    assert e.state == "idle"


def test_idle_cancel_or_timeout_does_nothing():
    e = Entry()
    assert e.cancel(0) == []
    assert e.tick(100.0) == []


def test_timeout_restarts_on_each_key():
    e = Entry(timeout_ms=10000)
    e.key("B", 0.0)
    e.key("C", 8.0)  # replacing the letter is a key press too
    assert e.tick(17.9) == []
    assert e.tick(18.0) == [changed("idle", reason="timeout")]


def test_both_orders_select_the_same_slot():
    a, b = Entry(), Entry()
    a.key("K", 0)
    b.key("0", 0)
    assert a.key("0", 1)[0] == b.key("K", 1)[0] == Selection("K0", 99)


def test_rejects_non_selection_keys():
    with pytest.raises(ValueError):
        Entry().key("I", 0)
