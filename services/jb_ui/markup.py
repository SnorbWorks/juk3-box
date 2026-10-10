"""Block markup: the engine's default templates, a theme's overrides, and
the cleaning every template gets before it reaches the screen.

Themes are data only (docs/theme-format.md, "Rules"). The page's security
policy already stops scripts from running; cleaning here is the second
layer, and it also points a template's own image paths at its theme
folder.
"""

import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

ALLOWED_TAGS = {
    "div", "span", "p", "img", "br", "hr", "small", "strong", "em", "b", "i",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "section", "header", "footer", "figure", "figcaption",
    # Inline SVG icons: shapes only (no <use>, <image> or <foreignObject>).
    "svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon",
}
# Dropped together with everything inside them.
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "foreignobject"}
VOID_TAGS = {"img", "br", "hr"}

ALLOWED_ATTRS = {
    "class", "id", "alt", "title", "role", "src", "style",
    # SVG presentation
    "viewbox", "xmlns", "d", "fill", "stroke", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "fill-rule", "opacity", "transform", "cx", "cy", "r", "rx", "ry",
    "x", "y", "x1", "y1", "x2", "y2", "width", "height", "points",
    "clip-rule", "fill-opacity", "stroke-opacity", "stroke-miterlimit", "stroke-dasharray",
}
UNSAFE_STYLE_RE = re.compile(r"url\s*\(|expression\s*\(|@import|javascript:", re.I)


def sanitize(html: str, base: str) -> str:
    """Keep only allowed tags and attributes. Relative `src` paths are
    pointed at `base` (the theme's URL); anything else in `src` is
    dropped."""
    cleaner = _Cleaner(base)
    cleaner.feed(html)
    cleaner.close()
    return "".join(cleaner.out)


class _Cleaner(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: list[str] = []
        self.dropping = 0      # depth inside a DROP_WITH_CONTENT element
        self.open: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.dropping += 1
            return
        if self.dropping or tag not in ALLOWED_TAGS:
            return
        self.out.append(f"<{_svg_case(tag)}{self._attrs(attrs)}>")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if self.dropping or tag in DROP_WITH_CONTENT or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{self._attrs(attrs)}>")
        else:
            self.out.append(f"<{_svg_case(tag)}{self._attrs(attrs)}></{_svg_case(tag)}>")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.dropping = max(0, self.dropping - 1)
            return
        if self.dropping or tag not in self.open:
            return
        while self.open:
            t = self.open.pop()
            self.out.append(f"</{_svg_case(t)}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.dropping:
            self.out.append(escape(data, quote=False))

    def close(self):
        super().close()
        while self.open:
            self.out.append(f"</{_svg_case(self.open.pop())}>")

    def _attrs(self, attrs) -> str:
        kept = []
        for name, value in attrs:
            value = value or ""
            if name.startswith("data-") or name.startswith("aria-"):
                pass
            elif name not in ALLOWED_ATTRS:
                continue
            if name == "src":
                value = self._src(value)
                if value is None:
                    continue
            if name == "style" and UNSAFE_STYLE_RE.search(value):
                continue
            kept.append(f' {_svg_case(name)}="{escape(value, quote=True)}"')
        return "".join(kept)

    def _src(self, value: str) -> str | None:
        if not value or "://" in value or value.startswith(("//", "/", "data:", "javascript:")) or ":" in value.split("/")[0]:
            return None
        if ".." in PurePosixPath(value).parts:
            return None
        return self.base + value


def _svg_case(name: str) -> str:
    # The parser lowercases everything; SVG needs these two back.
    return {"viewbox": "viewBox", "foreignobject": "foreignObject"}.get(name, name)


def load_blocks(engine_dir: Path, theme_dir: Path, theme_base: str) -> dict[str, str]:
    """Every block template by name: the engine's defaults, replaced by any
    the theme provides in its blocks/ folder."""
    blocks: dict[str, str] = {}
    for folder, base in ((engine_dir, "/engine/"), (theme_dir / "blocks", theme_base)):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.html")):
            if NAME_RE.match(path.stem):
                blocks[path.stem] = sanitize(path.read_text(), base)
    return blocks
