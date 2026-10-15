from services.jb_core.main import Queue
from services.jb_core.sources import LOCAL, PHONE, playable, source_of

DRIVE_SONG = {"title": "Tunnel", "item_path": "drive:6AD0/Music/Tunnel.wav"}
PHONE_SONG = {"title": "Animal", "item_path": "/org/bluez/hci0/dev_X/player0/Filesystem/item7"}


def test_drive_songs_go_to_the_local_source_and_the_rest_to_the_phone():
    assert source_of(DRIVE_SONG) == LOCAL
    assert source_of(PHONE_SONG) == PHONE
    assert source_of(None) == PHONE


def test_drive_songs_play_without_a_phone():
    assert playable(DRIVE_SONG, phone_connected=False)
    assert not playable(PHONE_SONG, phone_connected=False)
    assert playable(PHONE_SONG, phone_connected=True)


def test_shuffle_without_a_phone_picks_only_drive_songs():
    q = Queue()
    q.library_tracks = [DRIVE_SONG, PHONE_SONG]
    q.shuffle_active = True
    q.refill_shuffle()
    assert q.shuffle_buffer and all(t is DRIVE_SONG for t in q.shuffle_buffer)


def test_shuffle_with_one_song_doesnt_hang():
    q = Queue()
    q.library_tracks = [DRIVE_SONG]
    q.shuffle_active = True
    q.refill_shuffle()
    assert len(q.shuffle_buffer) == 20


def test_a_phone_song_waiting_for_the_phone_doesnt_hold_up_drive_songs():
    q = Queue()
    q.manual = [PHONE_SONG, DRIVE_SONG]
    assert q.pop_next() is DRIVE_SONG
    assert q.manual == [PHONE_SONG]          # keeps its place for when the phone is back
    assert q.pop_next() is None
    q.phone_connected = True
    assert q.pop_next() is PHONE_SONG


def test_shuffle_drops_phone_picks_when_the_phone_goes():
    q = Queue()
    q.library_tracks = [DRIVE_SONG, PHONE_SONG]
    q.shuffle_active, q.phone_connected = True, True
    q.shuffle_buffer = [PHONE_SONG] * 20
    q.phone_connected = False
    assert q.pop_next() is DRIVE_SONG
