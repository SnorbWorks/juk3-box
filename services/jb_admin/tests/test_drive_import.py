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


def test_a_drives_pictures_and_videos_are_imported_once(tmp_path, monkeypatch):
    imp, media = setup(tmp_path, monkeypatch)
    imp.heard(library(scanning=True))
    assert imp.queue.empty()                       # not until the scan is done
    imp.heard(library())
    drain(imp)
    assert media.added == ["crowd.png", "spin.mp4"]
    (media.folder / "crowd.png").unlink()          # the owner deletes one
    again = di.DriveImport(media, tmp_path / "imports.json", logging.getLogger("t"), 10, 600)
    again.heard(library())                         # drive plugged in again, after a restart
    drain(again)
    assert media.added == ["crowd.png", "spin.mp4"]


def test_the_media_library_cap_is_kept(tmp_path, monkeypatch):
    imp, media = setup(tmp_path, monkeypatch, cap_mb=0)
    imp.heard(library())
    drain(imp)
    assert media.added == []
    assert [s["reason"] for s in imp.status["6AD0"]["skipped"]] == ["media library full"] * 2
