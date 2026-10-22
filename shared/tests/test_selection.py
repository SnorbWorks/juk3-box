from shared.selection import DEFAULT, PRESETS, Format, clean, load


def test_the_default_is_a_to_k_and_1_to_0():
    f = load(None)
    assert f.page_size == 100 and f.codes[0] == "A1" and f.codes[9] == "A0" and f.codes[-1] == "K0"
    assert f.index("J1") == 80 and f.index("I1") is None
    assert f.describe() == "100 songs a page, A1 to K0"


def test_presets_are_valid_formats():
    for p in PRESETS.values():
        assert clean(p) is not None
    assert Format(clean(PRESETS["160"])).describe() == "160 songs a page, A1 to V8"
    assert Format(clean(PRESETS["rockola"])).describe() == "200 songs a page, 100 to 299"


def test_letters_and_digits_come_out_in_keypad_order():
    assert clean({"kind": "letters", "letters": "KBA", "digits": "0213"}) == \
        {"kind": "letters", "letters": "ABK", "digits": "1230"}


def test_unusable_formats_are_refused():
    assert clean({"kind": "letters", "letters": "", "digits": "1"}) is None
    assert clean({"kind": "letters", "letters": "AB!", "digits": "1"}) is None
    assert clean({"kind": "letters", "letters": "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "digits": "1234567890" * 2}) is not None
    assert clean({"kind": "numbers", "places": 3, "first": 300, "last": 100}) is None
    assert clean({"kind": "numbers", "places": 3, "first": 0, "last": 999}) is None     # 1000 a page: too many
    assert clean({"kind": "numbers", "places": True, "first": 1, "last": 2}) is None
    assert clean("A-K") is None
    assert load({"kind": "nope"}).spec == DEFAULT


def test_groups_are_letters_or_tens():
    letters = Format(clean(PRESETS["160"])).groups()
    assert len(letters) == 20 and letters[0].label == "A" and letters[0].codes == tuple(f"A{d}" for d in "12345678")
    tens = Format(clean(PRESETS["rockola"])).groups()
    assert len(tens) == 20 and tens[0].label == "100–109" and tens[-1].codes[-1] == "299"
