import json
import shutil
from pathlib import Path

import pytest

from services.jb_ui import themes

SHIPPED = Path(__file__).resolve().parents[3] / "themes"


def copy_bare(tmp_path, new_id="bare"):
    dest = tmp_path / new_id
    shutil.copytree(SHIPPED / "bare", dest)
    if new_id != "bare":
        m = json.loads((dest / "theme.json").read_text())
        m["id"] = new_id
        (dest / "theme.json").write_text(json.dumps(m))
    return dest


def edit_manifest(theme_dir, **changes):
    m = json.loads((theme_dir / "theme.json").read_text())
    m.update(changes)
    (theme_dir / "theme.json").write_text(json.dumps(m))


def write_layout(theme_dir, name, tree):
    (theme_dir / "layouts" / f"{name}.json").write_text(json.dumps(tree))


def problems(theme_id, dirs):
    with pytest.raises(themes.ThemeError) as e:
        themes.load_theme(theme_id, dirs)
    return " | ".join(e.value.problems)


def test_shipped_bare_theme_is_valid():
    t = themes.load_theme("bare", [SHIPPED])
    assert t.manifest["name"] == "Bare"
    assert set(t.layouts) >= {"frame", "home", "all-songs"}
    assert t.warnings == []


def test_missing_theme():
    assert problems("nope", [SHIPPED]) == "not installed"


def test_bad_ids_are_never_looked_up(tmp_path):
    assert themes.find_theme_dir("../etc", [tmp_path]) is None
    assert themes.find_theme_dir("Bare", [SHIPPED]) is None


def test_shipped_themes_win_over_added_ones_with_the_same_id(tmp_path):
    added = tmp_path / "added"
    copy_bare(added)
    assert themes.find_theme_dir("bare", [SHIPPED, added]) == SHIPPED / "bare"
    assert themes.list_themes([SHIPPED, added]).count("bare") == 1


def test_manifest_problems_are_all_reported(tmp_path):
    d = copy_bare(tmp_path)
    edit_manifest(d, format=2, id="other", name="", styles=["missing.css"])
    p = problems("bare", [tmp_path])
    assert "format must be 1" in p
    assert "doesn't match its folder name" in p
    assert "name is required" in p
    assert "missing.css not found" in p


@pytest.mark.parametrize("src", ["https://example.com/x.css", "/etc/passwd", "../bare/theme.css", "data:text/css,x"])
def test_files_must_be_inside_the_theme(tmp_path, src):
    d = copy_bare(tmp_path)
    edit_manifest(d, styles=[src])
    assert "must be a file inside the theme folder" in problems("bare", [tmp_path])


def test_canvas_sizing_needs_dimensions(tmp_path):
    d = copy_bare(tmp_path)
    edit_manifest(d, sizing={"mode": "canvas", "width": 1920})
    assert "positive whole-number height" in problems("bare", [tmp_path])
    edit_manifest(d, sizing={"mode": "canvas", "width": 1920, "height": 1080})
    themes.load_theme("bare", [tmp_path])


def test_required_layouts(tmp_path):
    d = copy_bare(tmp_path)
    (d / "layouts" / "home.json").unlink()
    assert "layouts/home.json is missing" in problems("bare", [tmp_path])


def test_frame_needs_exactly_one_screen(tmp_path):
    d = copy_bare(tmp_path)
    write_layout(d, "frame", {"children": [{"block": "logo"}]})
    assert "exactly one screen block, found 0" in problems("bare", [tmp_path])
    write_layout(d, "frame", {"children": [{"block": "screen"}, {"block": "screen"}]})
    assert "found 2" in problems("bare", [tmp_path])


def test_only_the_frame_holds_a_screen(tmp_path):
    d = copy_bare(tmp_path)
    write_layout(d, "home", {"children": [{"block": "screen"}]})
    assert "only the frame may hold a screen block" in problems("bare", [tmp_path])


def test_layout_node_rules(tmp_path):
    d = copy_bare(tmp_path)
    write_layout(d, "home", {"children": [
        {"block": "logo", "children": []},
        {"class": "bad class!", "children": []},
        {"block": "section", "type": {"nested": True}},
        {"class": "no-children"},
    ]})
    p = problems("bare", [tmp_path])
    assert "a block or a container, not both" in p
    assert "isn't a usable CSS class" in p
    assert "setting 'type' must be a simple value" in p
    assert "a container needs a children list" in p


def test_unknown_blocks_load_with_a_warning(tmp_path):
    d = copy_bare(tmp_path)
    write_layout(d, "home", {"children": [{"block": "weather", "city": "Austin"}]})
    t = themes.load_theme("bare", [tmp_path])
    assert any("unknown block 'weather'" in w for w in t.warnings)


def test_broken_json_is_reported(tmp_path):
    d = copy_bare(tmp_path)
    (d / "layouts" / "home.json").write_text("{ not json")
    p = problems("bare", [tmp_path])
    assert "layouts/home.json:" in p
    assert "home.json is missing" not in p


def test_drafts_win_over_published_owner_layouts_which_win_over_the_theme(tmp_path):
    theme = themes.load_theme("bare", [SHIPPED])
    draft, owner = tmp_path / "draft", tmp_path / "owner"
    draft.mkdir()
    owner.mkdir()
    (owner / "home.json").write_text(json.dumps({"class": "published", "children": []}))
    (draft / "home.json").write_text(json.dumps({"class": "draft", "children": []}))
    (owner / "extra.json").write_text(json.dumps({"children": []}))
    layouts, origins, found = themes.owner_layouts(theme, [("draft", draft), ("owner", owner)])
    assert layouts["home"]["class"] == "draft" and origins["home"] == "draft"
    assert origins["extra"] == "owner" and origins["frame"] == "theme"
    assert found == []
    assert theme.layouts["home"].get("class") != "draft"          # the theme itself is untouched


def test_a_broken_draft_falls_back_to_the_published_layout(tmp_path):
    theme = themes.load_theme("bare", [SHIPPED])
    draft, owner = tmp_path / "draft", tmp_path / "owner"
    draft.mkdir()
    owner.mkdir()
    (draft / "home.json").write_text(json.dumps({"children": [{"block": "screen"}]}))
    (owner / "home.json").write_text(json.dumps({"class": "published", "children": []}))
    layouts, origins, found = themes.owner_layouts(theme, [("draft", draft), ("owner", owner)])
    assert origins["home"] == "owner" and len(found) == 1


def test_check_layout():
    assert themes.check_layout("home", {"children": [{"block": "nav"}]}) == ([], [])
    assert themes.check_layout("frame", {"children": []})[0]
    assert themes.check_layout("../x", {"children": []})[0]
    assert themes.check_layout("home", {"children": [{"block": "rocket"}]})[1]      # unknown: warning only


def test_block_schema_matches_the_engine():
    import re
    here = Path(__file__).resolve().parents[1]
    schema = json.loads((here / "block_schema.json").read_text())
    js = (here / "engine" / "js" / "blocks.js").read_text()
    table = js[js.index("export const BLOCKS = {"):]
    table = table[:table.index("\n};")]
    engine = set(re.findall(r"^\s+'?([a-z-]+)'?:", table, re.M))
    assert set(schema["blocks"]) == themes.KNOWN_BLOCKS == engine
    groups = {g["id"] for g in schema["groups"]}
    for name, block in schema["blocks"].items():
        assert block["group"] in groups, name
        for key in block["settings"]:
            assert themes.SETTING_RE.match(key), (name, key)
