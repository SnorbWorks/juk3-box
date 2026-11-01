"""deploy/bluez/jb-bluetoothd: which bluetoothd bluetooth.service starts."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "bluez" / "jb-bluetoothd"
VERSION = "5.82-1.1+rpt2"


def executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


@pytest.fixture
def box(tmp_path):
    """The wrapper with its paths under tmp_path, a dpkg-query that reports
    tmp_path/installed, and two bluetoothds that say which they are."""
    text = SCRIPT.read_text()
    for old, new in {"/usr/local/lib/juk3-box/bluez": tmp_path / "builds",
                     "/usr/libexec/bluetooth/bluetoothd": tmp_path / "stock",
                     "/etc/juk3-box/bluez-stock": tmp_path / "bluez-stock",
                     "/run/jb-bluetoothd.failed": tmp_path / "failed",
                     "/usr/bin/dpkg-query": tmp_path / "dpkg-query"}.items():
        assert old in text
        text = text.replace(old, str(new))
    wrapper = executable(tmp_path / "jb-bluetoothd", text)
    (tmp_path / "builds").mkdir()
    executable(tmp_path / "builds" / f"bluetoothd-{VERSION}", '#!/bin/sh\necho "ours $*"\n')
    executable(tmp_path / "stock", '#!/bin/sh\necho "stock $*"\n')
    executable(tmp_path / "dpkg-query", f'#!/bin/sh\ncat "{tmp_path}/installed" 2>/dev/null || exit 1\n')
    (tmp_path / "installed").write_text(VERSION)

    def run(*args, **env):
        r = subprocess.run([str(wrapper), *args], capture_output=True, text=True,
                           env={"PATH": os.environ["PATH"], **env})
        assert r.returncode == 0, r.stderr
        return r.stdout.strip(), r.stderr.strip()
    run.dir = tmp_path
    return run


def test_ours_runs_when_built_for_the_installed_bluez(box):
    out, said = box("-n", "-d")
    assert out == "ours -n -d"
    assert VERSION in said


def test_the_systems_own_runs_for_any_other_bluez(box):
    (box.dir / "installed").write_text("5.82-1.1+rpt3")
    assert box()[0] == "stock"
    (box.dir / "installed").unlink()            # dpkg-query fails
    out, said = box("-n")
    assert out == "stock -n" and "not installed" in said


def test_the_systems_own_runs_when_chosen(box):
    (box.dir / "bluez-stock").write_text("")
    assert box()[0] == "stock"


def test_a_failure_of_ours_means_the_systems_own_until_the_next_start(box):
    assert box("--stopped", SERVICE_RESULT="success") == ("", "")
    assert box()[0] == "ours"                   # a normal stop (a restart) changes nothing
    box("--stopped")                            # no account of it: nothing either
    assert box()[0] == "ours"
    box("--stopped", SERVICE_RESULT="core-dump")
    out, said = box()
    assert out == "stock" and "core-dump" in said
