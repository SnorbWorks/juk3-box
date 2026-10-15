"""Extensions: everything JUKE.BOX runs or ships that isn't its own code
(deploy/extensions.json), with the version installed on this jukebox.

Not modules: a module is JUKE.BOX's own kind of add-on (Weather, Weather
Mix); an extension is someone else's software, font, web service or
picture that JUKE.BOX depends on, listed with its license and what that
license asks of us.
"""

import asyncio
import importlib.metadata
import json
import time
from pathlib import Path

CACHE_S = 600   # installed versions change only with an apt or pip upgrade


class Extensions:
    def __init__(self, path: Path):
        self.path = path
        self._versions: dict[str, str] = {}
        self._versions_at = 0.0

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as e:
            return {"items": [], "kinds": {}, "duties": {}, "problem": f"Can't read the list: {e}"}
        return data if isinstance(data, dict) else {"items": []}

    async def view(self) -> dict:
        data = self.read()
        items = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("id")]
        versions = await self.installed(items)
        for item in items:
            item["installed"] = versions.get(item["id"])
        return {**data, "items": items}

    async def installed(self, items: list[dict]) -> dict[str, str]:
        if time.monotonic() - self._versions_at < CACHE_S and self._versions:
            return self._versions
        out: dict[str, str] = {}
        system = {i["package"]: i["id"] for i in items if i.get("kind") in ("system", "dev") and i.get("package")}
        if system:
            for package, version in (await dpkg_versions(list(system))).items():
                out[system[package]] = version
        for item in items:
            if item.get("kind") in ("python", "dev") and item.get("package") and item["id"] not in out:
                try:
                    out[item["id"]] = importlib.metadata.version(item["package"])
                except importlib.metadata.PackageNotFoundError:
                    pass
        self._versions, self._versions_at = out, time.monotonic()
        return out


async def dpkg_versions(packages: list[str]) -> dict[str, str]:
    """{package: version} for the installed ones (none off a Debian system)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "dpkg-query", "-W", "-f", "${Package}\t${Version}\t${db:Status-Status}\n", *packages,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
    except (OSError, asyncio.TimeoutError):
        return {}
    versions = {}
    for line in out.decode(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == "installed":
            versions[parts[0]] = parts[1]
    return versions
