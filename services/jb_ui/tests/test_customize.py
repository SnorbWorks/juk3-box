import json
import shutil
from pathlib import Path

from services.jb_ui import customize, themes

SHIPPED = Path(__file__).resolve().parents[3] / "themes"


def neon():
    return themes.load_theme("neon", [SHIPPED])


def test_neon_declares_valid_options_and_styles():
    t = neon()
    problems = []
    customize.check(t.manifest, t.root, problems)
    assert problems == []
    assert {s["id"] for s in customize.styles_of(t.manifest)} >= {"midnight", "sunset", "emerald", "mono"}


def test_resolve_defaults_style_then_owner():
    t = neon()
    got, problems = customize.resolve(t.manifest, t.root, None)
    assert problems == [] and got["style"] == "midnight" and got["values"]["panel"] == "#0d1428"
    got, problems = customize.resolve(t.manifest, t.root, {"style": "mono", "values": {"accent": "#ff0000"}})
    assert problems == []
    assert got["values"]["panel"] == "#151517"           # the style
    assert got["values"]["cover-colors"] is False        # the style
    assert got["values"]["accent"] == "#ff0000"          # the owner, over the style
    assert got["values"]["roundness"] == 1               # the theme's default


def test_bad_owner_values_fall_back_without_breaking():
    t = neon()
    got, problems = customize.resolve(t.manifest, t.root, {"style": "gone", "values": {
        "panel": "blue", "roundness": 9, "font": "comic-sans", "background": "../../etc/passwd",
        "cover-colors": "yes", "unknown": 1}})
    assert len(problems) == 7
    assert got["style"] == "midnight" and got["values"]["panel"] == "#0d1428" and got["values"]["background"] == ""


def test_picture_values_stay_in_the_theme_or_media():
    t = neon()
    o = {"type": "picture"}
    assert customize.valid(o, "", t.root)
    assert customize.valid(o, "/media/stage.jpg", t.root)
    assert customize.valid(o, "assets/logo.png", t.root)
    assert not customize.valid(o, "/media/../x.jpg", t.root)
    assert not customize.valid(o, "../bare/theme.json", t.root)
    assert not customize.valid(o, "/etc/passwd", t.root)


def test_a_theme_with_broken_options_is_refused(tmp_path):
    shutil.copytree(SHIPPED / "bare", tmp_path / "bare")
    m = json.loads((tmp_path / "bare" / "theme.json").read_text())
    m["customize"] = {"options": [{"id": "x", "label": "X", "type": "color", "default": "nope"}],
                      "styles": [{"id": "s", "label": "S", "values": {"y": 1}}]}
    (tmp_path / "bare" / "theme.json").write_text(json.dumps(m))
    try:
        themes.load_theme("bare", [tmp_path])
        assert False, "should not load"
    except themes.ThemeError as e:
        assert any("default" in p for p in e.problems) and any("isn't an option" in p for p in e.problems)


def test_every_neon_color_option_is_used_by_its_css():
    t = neon()
    css = (t.root / "theme.css").read_text()
    for o in customize.options_of(t.manifest):
        if o.get("role") == "background":
            continue
        assert f"--opt-{o['id']}" in css or f'data-opt-{o["id"]}' in css, o["id"]


def test_font_library_is_usable_and_licensed():
    fonts = customize.font_library()
    assert len(fonts) >= 6
    for f in fonts:
        path = customize.FONTS_DIR / f["src"]
        assert path.is_file() and path.read_bytes()[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true"), f["id"]
        assert (customize.FONTS_DIR / f"{path.stem}-OFL.txt").is_file(), f["id"]
    t = neon()
    got, problems = customize.resolve(t.manifest, t.root, {"style": "midnight", "values": {"font": "oswald"}})
    assert problems == [] and got["values"]["font"] == "oswald"
