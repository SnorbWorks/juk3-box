"""Finding PipeWire nodes by name.

wpctl only takes numeric ids, and ids change whenever PipeWire or a node
restarts, so services look nodes up by name in `pw-dump` instead. The DAC is
the jukebox's own output: the first ALSA sink (the DAC Pro HAT is the only
sound card with a sink; HDMI has nothing plugged in).
"""

import asyncio
import json
import os
import subprocess

DAC_PREFIX = "alsa_output."


def _env() -> dict:
    return {**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")}


def nodes(objects: list) -> list[dict]:
    """[{id, name, media_class, props}] for every node in a pw-dump."""
    out = []
    for o in objects:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        props = (o.get("info") or {}).get("props") or {}
        out.append({"id": o["id"], "name": props.get("node.name"),
                    "media_class": props.get("media.class"), "props": props})
    return out


def find(objects: list, name: str) -> int | None:
    return next((n["id"] for n in nodes(objects) if n["name"] == name), None)


def find_dac(objects: list) -> dict | None:
    return next((n for n in nodes(objects)
                 if n["media_class"] == "Audio/Sink" and (n["name"] or "").startswith(DAC_PREFIX)), None)


def dump_sync(timeout: float = 5) -> list:
    out = subprocess.run(["pw-dump"], capture_output=True, text=True, env=_env(), timeout=timeout)
    return json.loads(out.stdout or "[]")


async def dump(timeout: float = 5) -> list:
    proc = await asyncio.create_subprocess_exec(
        "pw-dump", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=_env())
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return json.loads(out or b"[]")
