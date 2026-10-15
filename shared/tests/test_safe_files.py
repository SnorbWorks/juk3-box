import os

import pytest

from shared import files


def test_writes_and_replaces(tmp_path):
    target = tmp_path / "state" / "phones.json"
    files.write_atomic(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'
    assert oct(target.stat().st_mode & 0o777) == oct(0o644)
    files.write_atomic(target, b'{"a": 2}')
    assert target.read_text() == '{"a": 2}'
    assert [p.name for p in target.parent.iterdir()] == ["phones.json"]


def test_keeps_a_private_files_permissions(tmp_path):
    target = tmp_path / "auth.json"
    files.write_atomic(target, "secret", mode=0o600)
    files.write_atomic(target, "secret 2")
    assert oct(target.stat().st_mode & 0o777) == oct(0o600)


def test_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text("old")

    def broken(*args):
        raise OSError("power cut")
    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        files.write_atomic(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_replace_synced_moves_a_finished_file(tmp_path):
    part = tmp_path / "video.part"
    part.write_bytes(b"x" * 1000)
    dest = tmp_path / "video.mp4"
    files.replace_synced(part, dest)
    assert dest.read_bytes() == b"x" * 1000 and not part.exists()
