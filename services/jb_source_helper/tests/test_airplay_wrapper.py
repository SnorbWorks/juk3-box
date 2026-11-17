"""deploy/airplay/jb-airplay: which AirPlay receiver jb-airplay.service starts."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "airplay" / "jb-airplay"


def executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


@pytest.fixture
def box(tmp_path):
    """The wrapper with its paths under tmp_path; a systemctl that says jb-nqptp
    runs while tmp_path/nqptp-up exists; an ldd that prints tmp_path/ldd-says;
    and two receivers that say which they are and how they were started."""
    text = SCRIPT.read_text()
    for old, new in {"/usr/local/lib/juk3-box/airplay": tmp_path / "builds",
                     "/opt/juk3-box/deploy/airplay": tmp_path / "conf",
                     "/usr/bin/shairport-sync": tmp_path / "stock",
                     "/etc/juk3-box/airplay-classic": tmp_path / "airplay-classic",
                     "/run/jb-airplay.failed": tmp_path / "failed",
                     "/run/jb-airplay/ap2": tmp_path / "running-ap2"}.items():
        assert old in text
        text = text.replace(old, str(new))
    wrapper = executable(tmp_path / "jb-airplay", text)
    (tmp_path / "builds").mkdir()
    (tmp_path / "bin").mkdir()
    executable(tmp_path / "builds" / "shairport-sync",
               '#!/bin/sh\n[ "$1" = -V ] && { echo "5.6.3-AirPlay2-smi11"; exit 0; }\necho "ap2 $*"\n')
    executable(tmp_path / "stock", '#!/bin/sh\necho "classic $*"\n')
    executable(tmp_path / "bin" / "systemctl", f'#!/bin/sh\n[ -e "{tmp_path}/nqptp-up" ]\n')
    executable(tmp_path / "bin" / "ldd", f'#!/bin/sh\ncat "{tmp_path}/ldd-says" 2>/dev/null\n')
    (tmp_path / "nqptp-up").write_text("")
    (tmp_path / "ldd-says").write_text("\tlibplist-2.0.so.4 => /lib/libplist-2.0.so.4\n")

    def run(*args, **env):
        r = subprocess.run([str(wrapper), *args], capture_output=True, text=True,
                           env={"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", **env})
        assert r.returncode == 0, r.stderr
        return r.stdout.strip(), r.stderr.strip()
    run.dir = tmp_path
    return run


def test_airplay_2_runs_with_its_config_and_the_jukeboxs_name(box):
    out, said = box(JB_AIRPLAY_NAME="My Juk3box")
    assert out == f"ap2 -c {box.dir}/conf/shairport-sync-ap2.conf -a My Juk3box"
    assert "AirPlay 2 (5.6.3)" in said
    assert (box.dir / "running-ap2").exists()        # what --stopped looks for


def test_classic_runs_with_the_classic_config(box):
    (box.dir / "builds" / "shairport-sync").unlink()
    out, said = box()
    assert out == f"classic -c {box.dir}/conf/shairport-sync.conf"
    assert "no AirPlay 2 build" in said


def test_classic_when_chosen(box):
    (box.dir / "airplay-classic").write_text("")
    assert box()[0].startswith("classic")


def test_classic_without_nqptp(box):
    (box.dir / "nqptp-up").unlink()
    out, said = box()
    assert out.startswith("classic") and "NQPTP" in said


def test_classic_when_an_os_update_took_a_library_away(box):
    (box.dir / "ldd-says").write_text("\tlibavcodec.so.61 => not found\n")
    out, said = box()
    assert out.startswith("classic") and "libavcodec.so.61" in said


def test_a_failure_of_airplay_2_means_classic_until_the_next_start(box):
    box()                                            # AirPlay 2 ran
    assert box("--stopped", SERVICE_RESULT="success") == ("", "")
    assert box()[0].startswith("ap2")                # a normal stop (a restart) changes nothing
    box("--stopped", SERVICE_RESULT="exit-code")
    out, said = box()
    assert out.startswith("classic") and "exit-code" in said


def test_a_failure_of_classic_isnt_held_against_airplay_2(box):
    (box.dir / "airplay-classic").write_text("")
    box()                                            # classic ran: no AirPlay 2 note
    box("--stopped", SERVICE_RESULT="exit-code")
    assert not (box.dir / "failed").exists()
