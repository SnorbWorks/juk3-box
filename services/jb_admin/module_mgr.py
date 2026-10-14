"""Managing modules from the admin (docs/contracts/extensions.md): turning
them on and off, their logs and settings, and installing modules from a
zip -- checked, reviewed by the owner, sandboxed, health-checked, rolled
back if they fail.

The privileged steps (units, bus logins) go through jb-modctl: a request
file in a folder only this user can write, answered by systemd running
the root-owned helper (deploy/jb-modctl). No sudo.
"""

import asyncio
import json
import re
import secrets
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath

from services.jb_admin import places
from services.jb_admin.files import read_json, write_json
from services.jb_ui import module_blocks, themes

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TOPIC_RE = re.compile(r"^jukebox/[A-Za-z0-9_./+#-]+$")
RUN_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.py$")
ROLES = {"source", "analyzer", "input", "output", "service"}
SETTING_TYPES = {"text", "number", "enum", "toggle", "secret", "location"}
KEEP = {".py", ".json", ".html", ".css", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".md"}
KEEP_NAMES = {"LICENSE", "LICENCE", "README", "COPYING"}
MAX_FILES = 200
MAX_TOTAL = 20 * 1024 * 1024
HEALTH_S = 60
CONTRACTS = {1}


class ModuleError(Exception):
    pass


def unit_of(module: dict) -> str:
    return f"jbmod-{module['id']}" if module.get("installed") else module["id"]


# Checking a module -------------------------------------------------------------

def check_manifest(m, root: Path, taken: set[str]) -> list[str]:
    """What's wrong with an installed module's manifest. `taken` are the
    built-in modules' ids (an installed one can't replace them)."""
    if not isinstance(m, dict):
        return ["module.json must be an object"]
    p: list[str] = []
    mid = m.get("id")
    if m.get("contract") not in CONTRACTS:
        p.append(f"it's for contract version {m.get('contract')!r}; this jukebox knows {sorted(CONTRACTS)}")
    if not isinstance(mid, str) or not ID_RE.match(mid) or len(mid) > 40:
        return p + ["its id must be lowercase letters, numbers and hyphens"]
    if mid in taken or mid in ("jb-admin", "jb-modctl"):
        p.append(f"\"{mid}\" is one of the jukebox's own modules; an installed module needs another id")
    for key in ("name", "version"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            p.append(f"it needs a {key}")
    roles = m.get("roles")
    if not isinstance(roles, list) or not roles or not set(roles) <= ROLES:
        p.append(f"roles must be some of {', '.join(sorted(ROLES))}")
    run = m.get("run")
    if not isinstance(run, str) or not RUN_RE.match(run) or not (root / run).is_file():
        p.append("\"run\" must name its Python file, at the top of the module")
    for t in m.get("publishes", []) or []:
        if not isinstance(t, str) or not t.startswith(f"jukebox/{mid}/") or not TOPIC_RE.match(t):
            p.append(f"it may publish only under jukebox/{mid}/, not {t!r}")
    for t in m.get("subscribes", []) or []:
        if not isinstance(t, str) or not TOPIC_RE.match(t):
            p.append(f"{t!r} isn't a topic it can listen to")
    perms = m.get("permissions") or {}
    if not isinstance(perms, dict) or perms.get("network", "none") not in ("none", "internet"):
        p.append("permissions.network must be none or internet (local network access isn't available yet)")
    if perms.get("devices"):
        p.append("modules can't use devices (Bluetooth, audio, GPIO) yet")
    res = m.get("resources") or {}
    cpu, mem = res.get("cpu", 25), res.get("memory_mb", 128)
    if not isinstance(cpu, (int, float)) or not 1 <= cpu <= 100:
        p.append("resources.cpu must be 1 to 100 (percent of one core)")
    if not isinstance(mem, (int, float)) or not 32 <= mem <= 512:
        p.append("resources.memory_mb must be 32 to 512")
    os_ = m.get("owner_settings", {})
    if not isinstance(os_, dict):
        p.append("owner_settings must be an object")
    else:
        for k, s in os_.items():
            if not themes.SETTING_RE.match(k) or not isinstance(s, dict) or s.get("type") not in SETTING_TYPES:
                p.append(f"setting {k!r}: type must be one of {', '.join(sorted(SETTING_TYPES))}")
    for b in m.get("blocks", []) or []:
        p.extend(module_blocks.check_block(mid, root, b))
    return p


def review(m: dict) -> list[dict]:
    """What the owner is asked to approve, in plain words."""
    out = []
    net = (m.get("permissions") or {}).get("network", "none")
    out.append({"icon": "globe" if net == "internet" else "shield-check",
                "text": "Uses the internet (not your home network or this jukebox's other services)" if net == "internet"
                else "No internet or network access"})
    subs = [t for t in m.get("subscribes", []) or []]
    out.append({"icon": "radio", "text": "Listens to: " + ", ".join(subs) if subs else "Listens only to its own messages"})
    out.append({"icon": "radio", "text": f"Sends only its own messages (jukebox/{m['id']}/…)"})
    res = m.get("resources") or {}
    out.append({"icon": "cpu", "text": f"At most {res.get('cpu', 25)}% of one processor core and {res.get('memory_mb', 128)} MB of memory"})
    for b in m.get("blocks", []) or []:
        out.append({"icon": "puzzle", "text": f"Adds a \"{b.get('label')}\" block to the page builder"})
    if m.get("owner_settings"):
        out.append({"icon": "settings", "text": "Has settings: " + ", ".join(s.get("label", k) for k, s in m["owner_settings"].items())})
    return out


def _member(info: zipfile.ZipInfo) -> PurePosixPath | None:
    name = info.filename.replace("\\", "/")
    if info.is_dir():
        return None
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ModuleError(f"{name}: links aren't allowed in a module")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ModuleError(f"{name}: points outside the module")
    if path.parts[0] == "__MACOSX" or path.name.startswith("._") or path.name == ".DS_Store" \
            or "__pycache__" in path.parts:
        return None
    return path


# The manager ----------------------------------------------------------------------

class Manager:
    def __init__(self, modules_dir: Path, settings_dir: Path, secrets_dir: Path, modctl_dir: Path,
                 builtin_ids, registry, log):
        self.dir = modules_dir
        self.settings_dir = settings_dir
        self.secrets_dir = secrets_dir
        self.modctl = modctl_dir
        self.builtin_ids = builtin_ids        # callable -> set of built-in module ids
        self.registry = registry
        self.log = log
        self.staged: dict[str, dict] = {}     # token -> {"id", "path", "manifest", "at"}
        self.health_s = HEALTH_S
        self.secrets_changed = False

    # The privileged helper
    async def ctl(self, verb: str, *args: str, timeout: float = 90) -> None:
        name = f"{secrets.token_hex(8)}.json"
        req, resp = self.modctl / "requests" / name, self.modctl / "responses" / name
        if not (self.modctl / "requests").is_dir():
            raise ModuleError("The module manager isn't set up on this jukebox (deploy jb-admin again).")
        tmp = req.with_name(f".{name}")
        tmp.write_text(json.dumps({"verb": verb, "args": list(args)}))
        tmp.replace(req)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if resp.is_file():
                answer = read_json(resp, {})
                resp.unlink(missing_ok=True)
                if not answer.get("ok"):
                    raise ModuleError(answer.get("error") or "the module manager refused")
                return
            await asyncio.sleep(0.2)
        req.unlink(missing_ok=True)
        raise ModuleError("The module manager didn't answer.")

    # Installing
    def stage(self, zip_path: Path) -> dict:
        """Unpacks a module zip into a holding folder and checks it. Returns
        what the owner reviews; nothing runs."""
        try:
            zf = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError):
            raise ModuleError("That isn't a zip file.")
        with zf:
            members, total = [], 0
            for info in zf.infolist():
                path = _member(info)
                if path is None:
                    continue
                total += info.file_size
                members.append((info, path))
            if len(members) > MAX_FILES or total > MAX_TOTAL:
                raise ModuleError(f"Too big: a module may have {MAX_FILES} files and {MAX_TOTAL // 1048576} MB.")
            tops = {p.parts[0] for _, p in members}
            if len(tops) != 1 or not any(p.parts[1:] == ("module.json",) for _, p in members):
                raise ModuleError("The zip must hold one folder with module.json in it.")
            token = secrets.token_hex(8)
            holding = self.dir / ".staging" / token
            dest = holding / "module"
            dest.mkdir(parents=True)
            try:
                for info, path in members:
                    rel = PurePosixPath(*path.parts[1:])
                    if not rel.parts:
                        continue
                    if rel.suffix.lower() not in KEEP and rel.name not in KEEP_NAMES:
                        raise ModuleError(f"{rel}: modules can't include {rel.suffix or 'this kind of'} files")
                    target = dest.joinpath(*rel.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                    target.chmod(0o644)
                for d in [dest, *[p for p in dest.rglob("*") if p.is_dir()]]:
                    d.chmod(0o755)
                m = read_json(dest / "module.json")
                problems = check_manifest(m, dest, self.builtin_ids())
                if problems:
                    raise ModuleError("This module can't be installed: " + "; ".join(problems))
            except BaseException:
                shutil.rmtree(holding, ignore_errors=True)
                raise
        existing = read_json(self.dir / m["id"] / "module.json")
        self.staged[token] = {"id": m["id"], "path": dest, "manifest": m, "at": time.time()}
        self._prune()
        return {"token": token, "id": m["id"], "name": m["name"], "version": m["version"],
                "description": m.get("description", ""), "review": review(m),
                "update_from": existing.get("version") if isinstance(existing, dict) else None}

    def _prune(self):
        for token, s in list(self.staged.items()):
            if time.time() - s["at"] > 3600:
                self.discard(token)

    def discard(self, token: str) -> None:
        s = self.staged.pop(token, None)
        if s:
            shutil.rmtree(s["path"].parent, ignore_errors=True)

    async def install(self, token: str) -> dict:
        s = self.staged.pop(token, None)
        if not s:
            raise ModuleError("That upload has expired. Upload the module again.")
        mid = s["id"]
        target = self.dir / mid
        previous = self.dir / ".previous" / mid
        shutil.rmtree(previous, ignore_errors=True)
        if target.exists():
            previous.parent.mkdir(parents=True, exist_ok=True)
            target.rename(previous)
        s["path"].rename(target)
        shutil.rmtree(s["path"].parent, ignore_errors=True)
        self.dir.chmod(0o755)
        self._defaults(mid, s["manifest"])
        try:
            await self.ctl("install", mid)
            await self.wait_healthy(mid, self.health_s)
        except ModuleError as e:
            self.log.warning("module %s failed to install: %s", mid, e)
            await self._roll_back(mid, previous)
            raise ModuleError(f"{s['manifest']['name']} didn't start properly, so it was "
                              f"{'put back to the version before' if previous.exists() else 'removed'}: {e}")
        shutil.rmtree(previous, ignore_errors=True)
        self.log.info("installed module %s %s", mid, s["manifest"]["version"])
        return {"id": mid, "name": s["manifest"]["name"]}

    async def _roll_back(self, mid: str, previous: Path):
        shutil.rmtree(self.dir / mid, ignore_errors=True)
        if previous.exists():
            previous.rename(self.dir / mid)
            try:
                await self.ctl("install", mid)
                return
            except ModuleError:
                shutil.rmtree(self.dir / mid, ignore_errors=True)
        try:
            await self.ctl("remove", mid)
        except ModuleError:
            pass

    async def wait_healthy(self, mid: str, within: float = HEALTH_S) -> None:
        """It must heartbeat within `within` seconds, and still be up a few
        seconds later (not crashing in a loop)."""
        started = time.time()
        while time.time() - started < within:
            seen = self.registry.heartbeats.get(mid)
            if seen and seen >= started:
                await asyncio.sleep(4)
                if time.time() - self.registry.heartbeats.get(mid, 0) < 5:
                    return
            await asyncio.sleep(1)
        raise ModuleError(f"no heartbeat within {int(within)} seconds")

    async def uninstall(self, mid: str) -> None:
        if not ID_RE.match(mid) or not (self.dir / mid / "module.json").is_file():
            raise ModuleError("That module isn't installed.")
        await self.ctl("remove", mid)
        shutil.rmtree(self.dir / mid, ignore_errors=True)
        for f in (self.settings_dir / f"{mid}.json", self.secrets_dir / f"{mid}.json"):
            f.unlink(missing_ok=True)

    # Settings
    def _defaults(self, mid: str, m: dict) -> None:
        f = self.settings_dir / f"{mid}.json"
        if not f.exists():
            write_json(f, {})
        s = self.secrets_dir / f"{mid}.json"
        if not s.exists():
            write_json(s, {}, private=True)

    def get_settings(self, mid: str, m: dict) -> dict:
        schema = m.get("owner_settings") or {}
        values = read_json(self.settings_dir / f"{mid}.json", {}) or {}
        kept = read_json(self.secrets_dir / f"{mid}.json", {}) or {}
        out = {}
        for k, spec in schema.items():
            if spec.get("type") == "secret":
                out[k] = {"set": bool(kept.get(k))}
            else:
                out[k] = values.get(k, spec.get("default"))
        return {"schema": schema, "values": out}

    def save_settings(self, mid: str, m: dict, raw: dict) -> dict:
        """Checks the owner's values against the module's schema; secrets go
        to a private file (an empty value leaves a secret as it was)."""
        schema = m.get("owner_settings") or {}
        if not isinstance(raw, dict):
            raise ModuleError("Expected values.")
        values = read_json(self.settings_dir / f"{mid}.json", {}) or {}
        kept = read_json(self.secrets_dir / f"{mid}.json", {}) or {}
        kept_before = dict(kept)
        for k, v in raw.items():
            spec = schema.get(k)
            if not spec:
                continue
            t = spec.get("type")
            if t == "secret":
                if isinstance(v, str) and v:
                    kept[k] = v[:2000]
                elif v is None:
                    kept.pop(k, None)
                continue
            if t == "text" and isinstance(v, str):
                values[k] = v.strip()[:500]
            elif t == "number" and isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and spec.get("min", float("-inf")) <= v <= spec.get("max", float("inf")):
                values[k] = v
            elif t == "enum" and v in [o.get("value") for o in spec.get("options", [])]:
                values[k] = v
            elif t == "toggle" and isinstance(v, bool):
                values[k] = v
            elif t == "location" and places.clean(v):
                values[k] = places.clean(v)
            else:
                raise ModuleError(f"{spec.get('label', k)}: {v!r} isn't usable")
        write_json(self.settings_dir / f"{mid}.json", values)
        self.secrets_changed = kept != kept_before
        if self.secrets_changed:
            write_json(self.secrets_dir / f"{mid}.json", kept, private=True)
        return self.get_settings(mid, m)

    # Logs
    async def log_lines(self, unit: str, n: int = 150) -> list[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", f"{unit}.service", "-n", str(n), "--no-pager", "-o", "short-iso",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
        except (OSError, asyncio.TimeoutError):
            return ["The log couldn't be read."]
        return out.decode(errors="replace").splitlines()[-n:]
