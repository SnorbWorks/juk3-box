"""A release's latest.json (what jukeboxes update from), imager.json
(Raspberry Pi Imager's list, one entry: this version) and its notes.
Run by .github/workflows/release.yml: TAG OUT_DIR OWNER/REPO."""

import hashlib
import json
import sys
from pathlib import Path

tag, out, repo = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
base = f"https://github.com/{repo}/releases/download/{tag}"
image = json.loads((out / f"juk3box-{tag}.json").read_text())
code = out / f"juk3box-code-{tag}.tar.gz"

(out / "latest.json").write_text(json.dumps({
    "version": tag,
    "date": image["release_date"],
    "code": f"{base}/{code.name}",
    "code_sha256": hashlib.sha256(code.read_bytes()).hexdigest(),
    "signature": f"{base}/{code.name}.sig",
    "image": f"{base}/{image['file']}",
}, indent=2) + "\n")

(out / "imager.json").write_text(json.dumps({"os_list": [{
    "name": f"Juk3box {tag}",
    "description": "The Juk3box jukebox. Write the card, start the Pi, and set it up from your phone.",
    "icon": f"https://raw.githubusercontent.com/{repo}/master/deploy/release/imager-icon.png",
    "url": f"{base}/{image['file']}",
    "extract_size": image["extract_size"],
    "extract_sha256": image["extract_sha256"],
    "image_download_size": image["image_download_size"],
    "image_download_sha256": image["image_download_sha256"],
    "release_date": image["release_date"],
    "devices": ["pi5-64bit", "pi4-64bit"],
}]}, indent=2) + "\n")

(out / "notes.md").write_text(
    f"**A new jukebox:** write `juk3box-{tag}.img.xz` to a card with Raspberry Pi Imager "
    "(see the README), put it in the Pi and start it.\n\n"
    "**A jukebox that's already set up** updates itself (Admin › Setup › Updates).\n")
