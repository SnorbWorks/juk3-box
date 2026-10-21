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


def test_no_reconnecting_while_the_app_has_the_jukebox():
    assert rc.window_for("drop", was_playing=True, app_has_it=True) is None
    assert rc.window_for("boot", app_has_it=True) is None
    assert rc.window_for("pick", app_has_it=True) == 180


def test_the_bluetooth_phone_is_let_go_when_the_app_takes_over():
    assert rc.app_took_over("phone", "helper", first=False)
    assert rc.app_took_over(None, "helper", first=False)
    assert not rc.app_took_over("helper", "helper", first=False)   # a rename, a republish
    assert not rc.app_took_over(None, "helper", first=True)         # the choice as it stood at start
    assert not rc.app_took_over("helper", "phone", first=False)
