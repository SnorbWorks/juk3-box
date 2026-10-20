from types import SimpleNamespace

from services.jb_audio import airplay


def item(member, name, flags=4):
    return SimpleNamespace(interface=airplay.BROWSER_IFACE, member=member,
                           body=[2, 0, name, "_raop._tcp", "local", flags])


def test_receivers_are_listed_by_name_but_never_the_jukebox_itself():
    r = airplay.Receivers()
    r._signal(item("ItemNew", "30EB1C9EFC2D@Bar/Dining"))
    r._signal(item("ItemNew", "161940F425FC@JUKE.BOX", flags=4 | airplay.LOCAL | airplay.OUR_OWN))
    assert r.names == ["Bar/Dining"]
    r._signal(item("ItemRemove", "30EB1C9EFC2D@Bar/Dining"))
    assert r.names == []
