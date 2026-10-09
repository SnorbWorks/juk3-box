from pathlib import Path

from services.jb_ui import markup

BASE = "/themes/t/"


def clean(html):
    return markup.sanitize(html, BASE)


def test_plain_markup_survives():
    html = '<div class="card"><span data-text="title"></span><img data-src="cover" alt=""></div>'
    assert clean(html) == html


def test_scripts_and_their_content_are_removed():
    assert clean('<div>a<script>alert(1)</script>b</div>') == "<div>ab</div>"
    assert clean('<div><style>body{}</style><iframe src="x"></iframe></div>') == "<div></div>"


def test_event_handlers_and_unknown_attributes_are_removed():
    assert clean('<div onclick="x()" onmouseover="y" class="a">t</div>') == '<div class="a">t</div>'
    assert clean('<a href="javascript:x()">t</a>') == "t"  # <a> isn't allowed; its text is


def test_relative_src_points_at_the_theme():
    assert clean('<img src="assets/x.png">') == f'<img src="{BASE}assets/x.png">'


def test_outside_or_escaping_src_is_dropped():
    for src in ("https://evil.com/x.png", "//evil.com/x.png", "/etc/x", "../other/x.png", "javascript:x", "data:image/png;base64,AA"):
        assert clean(f'<img src="{src}">') == "<img>", src


def test_styles_cannot_load_anything():
    assert clean('<div style="color: red">t</div>') == '<div style="color: red">t</div>'
    assert clean('<div style="background: url(https://evil.com/x)">t</div>') == "<div>t</div>"


def test_svg_icons_keep_their_case():
    html = '<svg viewBox="0 0 24 24"><path d="M0 0h24"/></svg>'
    assert clean(html) == '<svg viewBox="0 0 24 24"><path d="M0 0h24"></path></svg>'


def test_text_is_escaped_and_unclosed_tags_are_closed():
    assert clean("<div>1 &lt; 2") == "<div>1 &lt; 2</div>"


def test_theme_blocks_replace_engine_defaults(tmp_path):
    engine = tmp_path / "engine"
    theme = tmp_path / "theme"
    engine.mkdir()
    (theme / "blocks").mkdir(parents=True)
    (engine / "now-playing.html").write_text("<div>engine</div>")
    (engine / "up-next.html").write_text("<div>engine</div>")
    (theme / "blocks" / "now-playing.html").write_text('<div>theme<img src="a.png"></div>')
    (theme / "blocks" / "Bad Name.html").write_text("<div>ignored</div>")
    blocks = markup.load_blocks(engine, theme, BASE)
    assert blocks == {
        "now-playing": f'<div>theme<img src="{BASE}a.png"></div>',
        "up-next": "<div>engine</div>",
    }


def test_engine_defaults_are_unchanged_by_cleaning():
    engine = Path(__file__).resolve().parents[1] / "engine" / "blocks"
    for path in engine.glob("*.html"):
        original = "".join(line.strip() for line in path.read_text().splitlines())
        cleaned = "".join(line.strip() for line in markup.sanitize(path.read_text(), "/engine/").splitlines())
        assert cleaned == original, path.name
