import asyncio
import logging
import sys

from services.jb_source_local import player


def test_a_failed_start_never_leaves_an_mpv_running(tmp_path, monkeypatch):
    """mpv that never opens its socket: each retry must kill the last one."""
    monkeypatch.setattr(player, "SOCKET", tmp_path / "mpv.sock")
    monkeypatch.setattr(player, "START_WAIT_S", 0.2)
    monkeypatch.setattr(player, "MPV", [sys.executable, "-c", "import time; time.sleep(60)"])
    started = []
    real_exec = asyncio.create_subprocess_exec

    async def spy(*args, **kw):
        proc = await real_exec(*args, **kw)
        started.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    async def go():
        async def changed(**kw):
            pass
        p = player.Player(logging.getLogger("test"), changed)
        task = asyncio.create_task(p.run())
        while len(started) < 3:
            await asyncio.sleep(0.05)
        task.cancel()
        alive = [proc for proc in started[:-1] if proc.returncode is None]
        for proc in started:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        return alive

    assert asyncio.run(go()) == []
