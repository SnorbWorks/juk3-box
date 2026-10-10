from services.jb_ui import icons


def test_library_icons_become_clean_svg():
    s = icons.svg("lucide:house")
    assert s.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"')
    assert 'stroke="currentColor"' in s and "<path" in s


def test_aliases_are_followed():
    assert icons.svg("lucide:x-circle") == icons.svg("lucide:circle-x")


def test_unknown_icons_and_sets():
    assert icons.svg("lucide:no-such-icon") is None
    assert icons.svg("nosuchset:house") is None
    assert icons.svg("../etc:passwd") is None
    assert icons.svg("house") is None


def test_every_engine_default_resolves():
    resolved = icons.resolve({}, {})
    assert set(resolved) == set(icons.DEFAULT_ICONS)
    assert resolved["now-playing"] == {"builtin": "eq"}


def test_theme_choices_and_files_win():
    resolved = icons.resolve({"home": "lucide:music"}, {"settings": "/themes/t/assets/icons/settings.svg"})
    assert resolved["home"]["svg"] == icons.svg("lucide:music")
    assert resolved["settings"] == {"url": "/themes/t/assets/icons/settings.svg"}
