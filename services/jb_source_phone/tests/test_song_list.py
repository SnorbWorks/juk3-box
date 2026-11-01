import asyncio

from services.jb_source_phone import main
from services.jb_source_phone import songs as sg

MAC = "AA:BB:CC:12:34:56"
PLAYER = "/org/bluez/hci0/dev_AA_BB_CC_12_34_56/player0"
UNDER = "/Filesystem/item72057594037927936/item216172782113783808"
SONGS = PLAYER + UNDER
LATER = SONGS.replace("player0", "player1")       # the same phone, a later connection


class V:
    """A D-Bus Variant, as dbus_next hands them over."""
    def __init__(self, value):
        self.value = value


def item(uid, title, **meta):
    props = {"Name": V(title), "Type": V("audio")}
    if meta:
        props["Metadata"] = V({k: V(v) for k, v in meta.items()})
    return f"{SONGS}/item{uid}", props


def listed(uid, title, folder=SONGS, **details):
    return {"item_path": f"{folder}/item{uid}", "title": title, **details}


def library(n, folder=SONGS):
    return [listed(1000 + i, f"Song {i}", folder, artist=f"Artist {i % 7}") for i in range(n)]


def test_a_songs_id_is_the_last_item_number_of_its_play_path():
    assert sg.uid_of(f"{SONGS}/item1152921504606847081") == "1152921504606847081"
    assert sg.uid_of(f"{SONGS}/item12") == "12"
    assert sg.uid_of(None) is None and sg.uid_of("/org/bluez/hci0/player0") is None
    assert sg.folder_of(f"{SONGS}/item12") == SONGS


def test_a_listed_song_keeps_the_details_the_phone_sent():
    path, props = item(7, "Animal", Artist="Neon Trees", Album="Habits", TrackNumber=3, Duration=212000)
    assert sg.track_from_item(path, props) == {
        "item_path": path, "title": "Animal", "artist": "Neon Trees", "album": "Habits",
        "track_number": 3, "duration_ms": 212000}


def test_details_the_phone_left_out_or_blank_arent_stored():
    path, props = item(7, "Animal", Artist="  ", Album="", TrackNumber=0, Duration=0)
    assert sg.track_from_item(path, props) == {"item_path": path, "title": "Animal"}
    path, props = item(8, "Plain")                       # BlueZ 5.66: the title alone
    assert sg.track_from_item(path, props) == {"item_path": path, "title": "Plain"}


def test_the_title_falls_back_to_the_metadatas():
    path = f"{SONGS}/item9"
    props = {"Type": V("audio"), "Metadata": V({"Title": V("From metadata")})}
    assert sg.track_from_item(path, props)["title"] == "From metadata"


def test_spot_checks_take_the_first_the_last_and_evenly_between():
    assert sg.spot_indices(0) == []
    assert sg.spot_indices(3) == [0, 1, 2]
    assert sg.spot_indices(5) == [0, 1, 2, 3, 4]
    assert sg.spot_indices(913) == [0, 228, 456, 684, 912]
    for n in (6, 50, 1000, 4321):
        spots = sg.spot_indices(n)
        assert spots[0] == 0 and spots[-1] == n - 1 and len(spots) == sg.SPOT_CHECKS


def test_a_kept_list_comes_back_with_this_connections_play_paths():
    tracks = library(4)
    stored = sg.stored_form(tracks)
    assert stored[0] == {"uid": "1000", "title": "Song 0", "artist": "Artist 0"}
    again = sg.rebased(stored, LATER)
    assert again == library(4, LATER)


def test_a_list_with_a_song_lacking_an_id_or_title_isnt_kept(tmp_path):
    assert sg.stored_form([listed(1, "Fine"), {"item_path": f"{SONGS}/folder", "title": "x"}]) == []
    assert sg.stored_form([listed(1, "Fine"), listed(2, None)]) == []
    assert sg.save(MAC, [listed(1, None)], PLAYER, store=tmp_path) is False
    assert sg.save(MAC, library(3), "/some/other/player", store=tmp_path) is False   # not under it
    assert list(tmp_path.iterdir()) == []


def test_where_songs_sits_under_the_player_carries_to_a_later_connection():
    assert sg.under_player(f"{SONGS}/item5", PLAYER) == UNDER
    assert sg.under_player(f"{SONGS}/item5", PLAYER + "0") is None    # player00 isn't player0
    later_player = PLAYER.replace("player0", "player1")
    assert later_player + sg.under_player(f"{SONGS}/item5", PLAYER) == LATER


def test_same_song_wants_the_id_and_the_title():
    stored = {"uid": "5", "title": "Animal"}
    assert sg.same_song(stored, listed(5, "Animal"))
    assert not sg.same_song(stored, listed(6, "Animal"))
    assert not sg.same_song(stored, listed(5, "Animals"))
    assert not sg.same_song({"uid": None, "title": None}, {"item_path": "x", "title": None})


def test_positions_are_by_id_first_place_wins():
    tracks = [listed(1, "A"), listed(2, "B"), listed(1, "A again")]
    assert sg.positions(tracks) == {"1": 0, "2": 1}


def test_save_load_forget(tmp_path):
    none = {"songs": [], "under_player": None}
    assert sg.load(MAC, tmp_path) == none
    assert sg.save(MAC, library(3), PLAYER, now=123.0, store=tmp_path)
    assert (tmp_path / "AA-BB-CC-12-34-56.json").exists()
    assert sg.load(MAC, tmp_path) == {"songs": sg.stored_form(library(3)), "under_player": UNDER}
    sg.forget(MAC, tmp_path)
    assert sg.load(MAC, tmp_path) == none
    sg.forget(MAC, tmp_path)                             # already gone: fine
    (tmp_path / "AA-BB-CC-12-34-56.json").write_text("{not json")
    assert sg.load(MAC, tmp_path) == none
    # A list kept before "under_player" was: read again, as if none.
    (tmp_path / "AA-BB-CC-12-34-56.json").write_text('{"at": 1, "songs": [{"uid": "1", "title": "A"}]}')
    assert sg.load(MAC, tmp_path) == none


# -- the phone side: checking and reading over a fake BlueZ ---------------------

class FakePhone(main.Phone):
    """A Phone whose Songs folder is a list; counts the BlueZ calls."""

    def __init__(self, songs):
        super().__init__(None, "/dev", "/player")
        self.songs = songs
        self.calls = []

    async def list_range(self, start, end):
        self.calls.append((start, end))
        return [dict(t) for t in self.songs[start:end + 1]]


def test_a_reconnect_with_the_same_songs_uses_the_kept_list():
    stored = sg.stored_form(library(913))
    p = FakePhone(library(913, LATER))
    tracks = asyncio.run(p.check_songs(stored, 913))
    assert tracks == library(913, LATER)                 # this connection's paths
    assert p.calls == [(i, i) for i in sg.spot_indices(913)]


def test_a_changed_library_fails_the_check():
    stored = sg.stored_form(library(913))
    assert asyncio.run(FakePhone(library(914, LATER)).check_songs(stored, 914)) is None
    swapped = library(913, LATER)
    swapped[456:] = swapped[457:] + [listed(5000, "New song", LATER)]   # one gone, one added
    assert asyncio.run(FakePhone(swapped).check_songs(stored, 913)) is None
    assert asyncio.run(FakePhone(library(3)).check_songs([], 3)) is None


def test_a_full_read_goes_in_chunks_and_says_how_far_along():
    p = FakePhone(library(250))
    seen = []

    async def progress(done, total):
        seen.append((done, total))
    assert asyncio.run(p.read_songs(250, progress)) == library(250)
    assert p.calls == [(0, 99), (100, 199), (200, 249)]
    assert seen == [(100, 250), (200, 250), (250, 250)]


def test_a_full_read_stops_when_the_phone_has_fewer_than_it_said():
    p = FakePhone(library(120))
    assert len(asyncio.run(p.read_songs(300))) == 120
    assert p.calls == [(0, 99), (100, 199), (120, 219)]
