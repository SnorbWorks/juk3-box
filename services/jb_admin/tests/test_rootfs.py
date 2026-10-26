"""The read-only system drive: deploy/jb-rootfs's own parts, jb-update's
restart to install, and the Updates tab's view of it."""

import importlib.machinery
import importlib.util
from pathlib import Path

from services.jb_admin import updates

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(DEPLOY / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


rootfs = load("jb-rootfs")
up = load("jb-update")

LINE = "console=serial0,115200 console=tty1 root=PARTUUID=18c49961-02 rootfstype=ext4 fsck.repair=yes rootwait\n"


def test_the_switch_adds_and_removes_only_the_overlay():
    on = rootfs.cmdline_with(LINE, True)
    assert on == f"{rootfs.OVERLAY} {LINE}"
    assert rootfs.cmdline_with(on, True) == on                     # once, however often
    assert rootfs.cmdline_with(on, False) == LINE
    assert rootfs.cmdline_with("overlayroot=tmpfs " + LINE, False) == LINE   # raspi-config's too


def test_fstab_binds_each_kept_folder_from_the_data_partition():
    base = "proc /proc proc defaults 0 0\nPARTUUID=x-02 / ext4 defaults,noatime 0 1\n"
    once = rootfs.fstab_with(base)
    assert once.startswith(base)
    assert "LABEL=jbdata  /data  ext4  defaults,noatime  0  2" in once
    for name, where in rootfs.DATA_DIRS:
        assert f"/data/{name}  {where}  none  bind,x-systemd.requires-mounts-for=/data  0  0" in once
    assert rootfs.fstab_with(once) == once                         # replaced, not added again


def test_the_overflow_swap_moves_to_the_data_partition():
    unit = "[Swap]\nWhat=/var/swapoverflow\nPriority=10\n"
    assert rootfs.swap_unit_with(unit) == "[Swap]\nWhat=/data/swapoverflow\nPriority=10\n"


def test_the_data_partition_goes_after_the_system_not_in_the_first_gap():
    # juk3box3's card (v0.1.2): boot at 16384, system at 1064960 for 8 GiB
    assert rootfs.data_start((1064960, 16777216)) == 17842176
    assert rootfs.data_start((1064960, 16777216 + 1)) == 17842176 + 2048   # rounded up to 1 MiB


def test_protection_waits_for_the_data_partition_to_be_finished(tmp_path, monkeypatch):
    import pytest
    flag = tmp_path / "make-data-partition"
    flag.touch()
    monkeypatch.setattr(rootfs, "FIRST_START", flag)
    monkeypatch.setattr(rootfs, "status", lambda: {"data": True, "overlayroot": True})
    with pytest.raises(rootfs.Refused):
        rootfs.switch(True)


def test_a_new_card_names_itself_from_its_serial(tmp_path, monkeypatch):
    serial = tmp_path / "serial-number"
    serial.write_text("10000000a1b2c3d4\0")
    monkeypatch.setattr(rootfs, "SERIAL", serial)
    assert rootfs.first_name() == "juk3box-c3d4"
    serial.write_text("")
    assert rootfs.first_name() == "juk3box"
    hosts = "127.0.0.1\tlocalhost\n127.0.1.1\tjuk3box\n"
    assert rootfs.hosts_with(hosts, "juk3box-c3d4") == "127.0.0.1\tlocalhost\n127.0.1.1\tjuk3box-c3d4\n"


def test_persist_refuses_anything_but_system_paths(monkeypatch):
    import pytest
    for bad in ("relative", "/etc/../root", "/proc/x", "/data/juk3-box"):
        with pytest.raises(rootfs.Refused):
            rootfs.persist([bad])


def test_updates_restart_to_install_when_protected(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(up, "RESUME", tmp_path / "resume.json")
    monkeypatch.setattr(up, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(up, "run", lambda *cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(up, "protected", lambda proc_cmdline=None: True)
    monkeypatch.setattr(up, "upgradable", lambda: ([{"name": "openssl", "from": "1", "to": "2"}], []))
    o = up.os_apply("security")
    assert o["phase"] == "to install"
    assert up.read_json(up.RESUME, {})["verbs"] == [["os-apply", "security"]]
    assert calls == [(up.ROOTFS, "off"), ("systemctl", "--no-block", "reboot")]


def test_resume_installs_then_protects_again_and_restarts(tmp_path, monkeypatch):
    calls, done = [], []
    monkeypatch.setattr(up, "RESUME", tmp_path / "resume.json")
    monkeypatch.setattr(up, "run", lambda *cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(up, "os_apply", lambda which: done.append(which) or {"phase": None})
    up.write_json(up.RESUME, {"verbs": [["os-apply", "all"]], "at": 1})
    up.resume()
    assert done == ["all"] and not up.RESUME.exists()
    assert calls == [(up.ROOTFS, "on"), ("systemctl", "--no-block", "reboot")]


def test_protected_reads_the_kernel_command_line():
    assert up.protected("overlayroot=tmpfs:recurse=0 console=tty1")
    assert not up.protected("console=tty1 root=PARTUUID=x")


def test_the_updates_tab_sees_the_protection(tmp_path):
    proc, boot = tmp_path / "proc", tmp_path / "boot"
    proc.write_text(LINE)
    boot.write_text(rootfs.cmdline_with(LINE, True))
    assert updates.rootfs_view(proc, boot, tmp_path) == {"protected": False, "next_start": True, "data": False}


def test_a_public_key_is_added_once_and_anything_else_is_refused(tmp_path):
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExample00 dev@laptop"
    assert updates.add_key(key, tmp_path)
    assert updates.add_key(key.replace("dev@laptop", "same key"), tmp_path)
    path = tmp_path / ".ssh" / "authorized_keys"
    assert path.read_text() == key + "\n" and oct(path.stat().st_mode & 0o777) == "0o600"
    for bad in ("", "AAAA", "ssh-ed25519", "ssh-dss AAAAB3 x", key + "\ncommand=rm ssh-rsa AAAA",
                "-----BEGIN OPENSSH PRIVATE KEY-----"):
        assert not updates.add_key(bad, tmp_path)
    assert path.read_text() == key + "\n"
