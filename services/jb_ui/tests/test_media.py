import asyncio
import logging
import os
import shutil
import subprocess

import pytest

from services.jb_ui import media

log = logging.getLogger("test")


def make_video(path, size="2560x1440", rate=24, seconds=1):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}",
                    "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,codec_name", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    codec, w, h, rate = out.split(",")
    return codec, int(w), int(h), rate


def test_copy_path_changes_with_the_file(tmp_path):
    src = tmp_path / "bg.mp4"
    src.write_bytes(b"one")
    v = media.VideoOptimizer(tmp_path / "cache", log)
    first = v.copy_path(src)
    src.write_bytes(b"one, longer")
    os.utime(src, ns=(1, 2))
    assert v.copy_path(src) != first
    assert v.copy_path(src).name.split("-")[0] == first.name.split("-")[0]  # same video, new state


def test_only_videos():
    assert media.VideoOptimizer.is_video(media.Path("a.MOV"))
    assert not media.VideoOptimizer.is_video(media.Path("a.png"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_big_video_is_served_as_a_720p30_h264_copy(tmp_path):
    src = tmp_path / "bg.mov"
    make_video(src)
    v = media.VideoOptimizer(tmp_path / "cache", log)

    async def go():
        assert v.served_path(src) == src          # not ready yet: the original
        await v.wait()
        return v.served_path(src)
    served = asyncio.run(go())
    assert served != src and served.parent == tmp_path / "cache"
    assert probe(served) == ("h264", 1280, 720, "30/1")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_small_video_keeps_its_size_and_old_copies_go(tmp_path):
    src = tmp_path / "bg.mp4"
    make_video(src, size="640x360", rate=30)
    v = media.VideoOptimizer(tmp_path / "cache", log)

    async def go(fresh):
        v2 = media.VideoOptimizer(tmp_path / "cache", log) if fresh else v
        v2.served_path(src)
        await v2.wait()
        return v2.served_path(src)
    first = asyncio.run(go(False))
    assert probe(first)[1:3] == (640, 360)       # never scaled up
    make_video(src, size="640x360", rate=30, seconds=2)
    second = asyncio.run(go(True))
    assert second != first and not first.exists()


def test_a_broken_video_falls_back_to_the_original(tmp_path):
    src = tmp_path / "bg.mp4"
    src.write_bytes(b"not a video")
    v = media.VideoOptimizer(tmp_path / "cache", log)

    async def go():
        v.served_path(src)
        await v.wait()
        return v.served_path(src)
    assert asyncio.run(go()) == src
