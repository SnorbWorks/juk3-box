from services.jb_source_phone import reconnect as rc


def test_policy_option_c():
    assert rc.window_for("boot") == 600
    assert rc.window_for("drop", was_playing=True) == 180
    assert rc.window_for("drop", was_playing=False) is None
    assert rc.window_for("pick") == 180


def test_retry_slows_after_the_first_minute():
    assert rc.retry_delay(10) == 15 and rc.retry_delay(120) == 30


def test_phone_book_orders_most_recent_first(tmp_path):
    book = rc.PhoneBook(tmp_path / "phones.json")
    book.touch("AA", "Old phone", 100)
    book.touch("BB", "New phone", 200)
    assert book.order(["AA", "CC", "BB"]) == ["BB", "AA", "CC"]
    again = rc.PhoneBook(tmp_path / "phones.json")       # survives a restart
    assert again.phones["BB"]["name"] == "New phone"
    book.touch("AA", "Old phone", 300)                   # one entry per phone, overwritten
    assert list(rc.PhoneBook(tmp_path / "phones.json").phones) == ["AA", "BB"]
