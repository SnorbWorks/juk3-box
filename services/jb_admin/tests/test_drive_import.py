import asyncio
import logging

from services.jb_admin import drive_import as di


class FakeMedia:
    def __init__(self, folder):
        self.folder = folder
        self.added = []

    async def add(self, tmp, filename):
        dest = self.folder / filename
        tmp.rename(dest)
        self.added.append(filename)
        return {"name": filename}


def setup(tmp_path, monkeypatch, cap_mb=10):
    mounts = tmp_path / "mounts"
    (mounts / "6AD0" / "Images").mkdir(parents=True)
    (mounts / "6AD0" / "Images" / "crowd.png").write_bytes(b"x" * 1000)
    (mounts / "6AD0" / "Videos").mkdir()
    (mounts / "6AD0" / "Videos" / "spin.mp4").write_bytes(b"y" * 2000)
    monkeypatch.setattr(di, "MOUNTS", mounts)
    media = FakeMedia(tmp_path / "media")
    imp = di.DriveImport(media, tmp_path / "imports.json", logging.getLogger("t"), cap_mb, 600)
    return imp, media


def library(scanning=False):
    return {"drives": [{"id": "6AD0", "label": "juk3box HD", "scanning": scanning,
                        "images": [{"path": "Images/crowd.png", "size": 1000}],
                        "videos": [{"path": "Videos/spin.mp4", "size": 2000}]}]}


def drain(imp):
    async def go():
        while not imp.queue.empty():
            drive_id, item = imp.queue.get_nowait()
            await imp.import_one(drive_id, item)
            imp.pending.discard(imp.key(drive_id, item))
    asyncio.run(go())


def test_nothing_is_imported_until_the_owner_says_so(tmp_path, monkeypatch):
    imp, media = setup(tmp_path, monkeypatch)
    imp.heard(library(scanning=True))
    assert imp.offers() == []                      # not until the scan is done
    imp.heard(library())
    assert imp.queue.empty()
    assert imp.offers() == [{"id": "6AD0", "label": "juk3box HD", "pictures": 1, "videos": 1}]
    assert imp.import_drive("6AD0") == 2
    assert imp.offers() == []                      # on their way: not asked again
    drain(imp)
    assert media.added == ["crowd.png", "spin.mp4"]
    assert imp.offers() == []


def test_not_now_holds_until_the_drive_is_plugged_in_again(tmp_path, monkeypatch):
    imp, media = setup(tmp_path, monkeypatch)
    imp.heard(library())
    imp.decline("6AD0")
    imp.heard(library())                           # a rescan: still not now
    assert imp.offers() == []
    imp.heard({"drives": []})                      # unplugged
    imp.heard(library())                           # and back
    assert [o["id"] for o in imp.offers()] == ["6AD0"]
    assert imp.queue.empty()


def test_a_drives_pictures_and_videos_are_imported_once(tmp_path, monkeypatch):
    imp, media = setup(tmp_path, monkeypatch)
    imp.heard(library())
    imp.import_drive("6AD0")
    drain(imp)
    (media.folder / "crowd.png").unlink()          # the owner deletes one
    again = di.DriveImport(media, tmp_path / "imports.json", logging.getLogger("t"), 10, 600)
    again.heard(library())                         # drive plugged in again, after a restart
    assert again.offers() == [] and again.import_drive("6AD0") == 0
    assert media.added == ["crowd.png", "spin.mp4"]


def test_the_media_library_cap_is_kept(tmp_path, monkeypatch):
    imp, media = setup(tmp_path, monkeypatch, cap_mb=0)
    imp.heard(library())
    imp.import_drive("6AD0")
    drain(imp)
    assert media.added == []
    assert [s["reason"] for s in imp.status["6AD0"]["skipped"]] == ["media library full"] * 2
    assert imp.offers() == []                      # skipped: not asked about again
