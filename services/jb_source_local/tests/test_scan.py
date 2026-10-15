import wave

from services.jb_source_local import scan


def make_wav(path, seconds=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * seconds)


def drive(tmp_path):
    root = tmp_path / "drive"
    make_wav(root / "Music" / "Album" / "Tunnel.wav", 2)
    (root / "Music" / "._Tunnel.wav").write_bytes(b"\0" * 4096)          # a Mac's shadow file
    (root / "Music" / "Old purchase.m4p").write_bytes(b"\0" * 100)
    (root / "Music" / "broken.mp3").write_bytes(b"not music")
    (root / ".Trashes").mkdir()
    make_wav(root / ".Trashes" / "deleted.wav")
    (root / "Images").mkdir()
    (root / "Images" / "crowd.png").write_bytes(b"\x89PNG")
    (root / "Videos").mkdir()
    (root / "Videos" / "spin.mp4").write_bytes(b"\0" * 10)
    return root


def test_a_drive_is_read_into_songs_pictures_videos_and_whats_skipped(tmp_path):
    result = scan.scan(drive(tmp_path), "6AD0")
    assert [(s["title"], s["item_path"], s["duration_ms"]) for s in result["songs"]] == [
        ("Tunnel", "drive:6AD0/Music/Album/Tunnel.wav", 2000)]          # no title tag: the file name
    assert result["images"] == [{"path": "Images/crowd.png", "size": 4}]
    assert result["videos"] == [{"path": "Videos/spin.mp4", "size": 10}]
    assert {s["path"]: s["reason"] for s in result["skipped"]} == {
        "Music/Old purchase.m4p": "protected", "Music/broken.mp3": "unreadable"}


def test_a_rescan_only_reads_files_that_changed(tmp_path, monkeypatch):
    root = drive(tmp_path)
    first = scan.scan(root, "6AD0")
    reads = []
    monkeypatch.setattr(scan, "read_song", lambda p: reads.append(p.name) or None)
    again = scan.scan(root, "6AD0", known=first["files"])
    assert reads == []
    assert again["songs"] == first["songs"]
    make_wav(root / "Music" / "New.wav")
    scan.scan(root, "6AD0", known=first["files"])
    assert reads == ["New.wav"]
