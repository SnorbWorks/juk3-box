#!/usr/bin/env python3
"""Is what the jukebox is built from still current? (THIRD-PARTY.md lists it.)

Compares what this repo pins or bundles with the newest release of each:
  - the Python packages (deploy/requirements.txt, pinned in deploy/install/constraints.txt), on PyPI
  - CircuitPython and the Adafruit HID library bundled for the keypad board
  - MQTT.js and the Lucide icons bundled for the screen and the admin

The system packages (Chromium, PipeWire, BlueZ, mosquitto...) come from
Raspberry Pi OS and are updated by it, not pinned here. The fonts are
checked by hand (google/fonts has no releases).

    python3 tools/check_updates.py            # a Markdown report; exit 1 if anything is behind
Run monthly by .github/workflows/check-updates.yml, which opens an issue.
Standard library only.
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "juk3box-check-updates",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def newer(have, latest) -> bool:
    """Is `latest` newer than `have`? Plain version numbers compare as numbers."""
    if have == latest:
        return False
    nums = [re.fullmatch(r"\d+(\.\d+)*", v or "") for v in (have, latest)]
    if all(nums):
        return tuple(map(int, latest.split("."))) > tuple(map(int, have.split(".")))
    return True


def python_packages():
    wanted = {re.split(r"[<>=\[ ]", line.strip())[0].lower()
              for line in (REPO / "deploy/requirements.txt").read_text().splitlines()
              if line.strip() and not line.startswith("#")}
    pins = dict(line.strip().split("==") for line in (REPO / "deploy/install/constraints.txt").read_text().splitlines()
                if "==" in line and not line.startswith("#"))
    for name in sorted(wanted):
        have = next((v for k, v in pins.items() if k.lower() == name), None)
        latest = fetch(f"https://pypi.org/pypi/{name}/json")["info"]["version"]
        yield f"{name} (Python)", have, latest


def circuitpython():
    uf2 = next((REPO / "firmware/pico-keypad/vendor").glob("adafruit-circuitpython-*.uf2"))
    have = re.search(r"-(\d+\.\d+\.\d+)\.uf2$", uf2.name).group(1)
    latest = fetch("https://api.github.com/repos/adafruit/circuitpython/releases/latest")["tag_name"]
    yield "CircuitPython (keypad board)", have, latest


def adafruit_hid():
    readme = (REPO / "firmware/pico-keypad/vendor/README.md").read_text()
    have = re.search(r"Adafruit CircuitPython HID[^|]*\|\s*([\d.]+)", readme).group(1)
    latest = fetch("https://api.github.com/repos/adafruit/Adafruit_CircuitPython_HID/releases/latest")["tag_name"]
    yield "Adafruit CircuitPython HID (keypad board)", have, latest


def mqtt_js():
    js = next((REPO / "services/jb_ui/engine").glob("mqtt-*.min.js"))
    have = re.search(r"mqtt-([\d.]+)\.min\.js", js.name).group(1)
    latest = fetch("https://registry.npmjs.org/mqtt/latest")["version"]
    yield "MQTT.js (screen)", have, latest


def lucide():
    """The bundled set says when it was made; newer if a release came after."""
    made = json.loads((REPO / "services/jb_ui/icons/lucide.json").read_text())["lastModified"]
    info = fetch("https://registry.npmjs.org/@iconify-json/lucide")
    latest = info["dist-tags"]["latest"]
    published = datetime.fromisoformat(info["time"][latest].replace("Z", "+00:00"))
    have_date = datetime.fromtimestamp(made, timezone.utc)
    behind = (published - have_date).days > 7
    yield "Lucide icons (@iconify-json/lucide)", f"set of {have_date:%Y-%m-%d}", latest if behind else f"set of {have_date:%Y-%m-%d}"


CHECKS = (python_packages, circuitpython, adafruit_hid, mqtt_js, lucide)


def main() -> int:
    rows, problems = [], []
    for check in CHECKS:
        try:
            rows += list(check())
        except Exception as e:  # one source being down mustn't hide the rest
            problems.append(f"{check.__name__}: {e}")
    behind = [(n, h, l) for n, h, l in rows if newer(h, l)]
    print("# Dependency check\n")
    if behind:
        print("Newer versions are out:\n\n| What | Ours | Newest |\n|---|---|---|")
        for n, h, l in behind:
            print(f"| {n} | {h or '?'} | {l} |")
        print("\nUpdate the pin or the bundled file, then run the tests on a jukebox before releasing. "
              "THIRD-PARTY.md lists where each one lives.")
    else:
        print("Everything checked is current.")
    if problems:
        print("\nCouldn't check:\n" + "\n".join(f"- {p}" for p in problems))
    print("\nNot checked here: Raspberry Pi OS packages (updated on each jukebox) and the fonts (by hand, yearly).")
    return 1 if behind or problems else 0


if __name__ == "__main__":
    sys.exit(main())
