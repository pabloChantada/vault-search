"""Notes rendered to lines with Rich, ready for the reader to paint.

Textual's Markdown widget mounts a widget per block, and laying those out is what made moving
through results slow: 250-750 ms for a 3 KB note, seconds for a long one. Rich renders the
same note to styled lines in about 5 ms, the reader paints only the lines on screen, and as
rendering touches no widget it can run in a thread, so the notes around the selected one are
rendered ahead of time (see `RenderCache`).

The note is rendered one section at a time (split at headings, outside code fences), which
gives the line where every heading starts: `]` / `[` and "jump to the section of the hit"
need nothing else.

Links stay links. Before rendering, every [[wikilink]], ![[embed]], ![image](path) and
[text](url) becomes a Markdown link to `vs:<n>`, where n indexes `Page.targets`. After
rendering, the segments carrying those links get an `@click` action instead (Textual runs it
on the reader, which also underlines it on hover), and `Page.links` records where each one
sits so the keyboard can reach them too. Images show as a caption (▣ name.png) that opens
them at full resolution (see tui/images.py): cells are too coarse for an inline thumbnail.
"""
from __future__ import annotations

import io
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import CodeBlock, Heading, Markdown
from rich.rule import Rule
from rich.segment import Segment
from rich.style import Style
from rich.syntax import Syntax
from rich.theme import Theme
from textual.strip import Strip

from ..core.links import IMAGE_EXTS, VaultFiles
from ..core.text import preview_markdown

_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
_WIKI_PARTS_RE = re.compile(r"^(!?)\[\[([^\]|#]*)(?:#([^\]|]*))?(?:\|([^\]]*))?\]\]$")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


@dataclass(frozen=True)
class Target:
    """Where a link goes: kind is "note", "missing" (no such note), "url" or "image"."""

    kind: str
    label: str
    path: Path | None = None
    heading: str = ""
    url: str = ""


@dataclass(frozen=True)
class Page:
    """A note as lines of one width, plus where each heading and link sits."""

    lines: list[Strip]
    headings: list[tuple[int, str]]  # (line, heading text)
    width: int
    targets: list[Target] = field(default_factory=list)
    links: list[tuple[int, int, int, int]] = field(default_factory=list)  # (line, x, cells, target)


def split_sections(text: str) -> list[tuple[str | None, str]]:
    """Cut Markdown before every heading that isn't inside a code fence: [(heading, source)]."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    fence: str | None = None
    for line in text.splitlines():
        if m := _FENCE_RE.match(line):
            mark = m.group(1)
            if fence is None:
                fence = mark[0] * 3
            elif mark.startswith(fence):
                fence = None
        elif fence is None and (h := _HEADING_RE.match(line)):
            sections.append((h.group(2).strip(), []))
        sections[-1][1].append(line)
    return [(h, "\n".join(body)) for h, body in sections if h is not None or "\n".join(body).strip()]


# ---------- links ----------
def _md_text(s: str) -> str:
    """Make `s` safe as the text of a Markdown link."""
    return re.sub(r"([\[\]\\*_`])", r"\\\1", s.strip()) or "…"


def _image_target(path: Path | None, name: str) -> Target:
    return Target("image", f"▣ {Path(name).name}", path)


def prepare(raw: str, note: Path | None, files: VaultFiles | None) -> tuple[str, list[Target]]:
    """The note as Markdown whose links all point at `vs:<n>`, plus the targets they stand for."""
    targets: list[Target] = []

    def add(target: Target) -> str:
        targets.append(target)
        return f"[{_md_text(target.label)}](vs:{len(targets) - 1})"

    def wikilink(m: re.Match) -> str:
        parts = _WIKI_PARTS_RE.match(m.group(0))
        if not parts:
            return m.group(0)
        embed, name, heading, alias = parts.groups()
        name, heading = name.strip(), (heading or "").strip()
        if embed and Path(name).suffix.lower() in IMAGE_EXTS:
            return add(_image_target(files.resolve(name) if files else None, name))
        label = alias or (f"{name} › {heading}" if name and heading else name or heading)
        if not name:  # [[#Heading]]: a section of this very note
            return add(Target("note", label, note, heading))
        found = files.note(name) if files else None
        return add(Target("note", label, found, heading) if found else Target("missing", label))

    def md_image(m: re.Match) -> str:
        alt, src = m.group(1), unquote(m.group(2))
        if _SCHEME_RE.match(src):
            return add(Target("url", f"▣ {alt or src}", url=src))
        found = None
        if note is not None and (note.parent / src).is_file():
            found = note.parent / src
        elif files is not None:
            found = files.resolve(src)
        return add(_image_target(found, alt or src))

    text = preview_markdown(raw, wikilink)
    return _MD_IMAGE_RE.sub(md_image, text), targets


def _href_target(href: str, note: Path | None, files: VaultFiles | None) -> Target:
    """A plain Markdown link: a web address, or a relative link to another note."""
    if _SCHEME_RE.match(href):
        return Target("url", href, url=href)
    rel, _, heading = unquote(href).partition("#")
    found = None
    if rel and note is not None and (note.parent / rel).is_file():
        found = note.parent / rel
    elif rel and files is not None:
        found = files.note(rel) if not Path(rel).suffix or rel.endswith(".md") else files.resolve(rel)
    if found is not None and found.suffix.lower() in IMAGE_EXTS:
        return _image_target(found, rel)
    return Target("note", rel, found, heading) if found else Target("missing", rel or href)


# ---------- rendering ----------
class _Heading(Heading):
    """Headings set flush left, the top one ruled underneath like the old reader."""

    LEVEL_ALIGN = dict.fromkeys(("h1", "h2", "h3", "h4", "h5", "h6"), "left")

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield from super().__rich_console__(console, options)
        if self.tag == "h1":
            yield Rule(style="markdown.h1.border", characters="▔")


class _CodeBlock(CodeBlock):
    """Code on the shaded paper instead of the syntax theme's own background."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        bg = console.get_style("markdown.code_block").bgcolor
        yield Syntax(
            str(self.text).rstrip(), self.lexer_name, theme=self.theme, word_wrap=True, padding=(0, 1),
            background_color=bg.name if bg else None,
        )


class _NoteMarkdown(Markdown):
    elements = {**Markdown.elements, "heading_open": _Heading, "fence": _CodeBlock, "code_block": _CodeBlock}


def rich_theme(p: dict[str, str]) -> Theme:
    """The reader's styles, from the same palette as the Textual themes (see vaultsearch/theme.py)."""
    deep = p["washi_deep"]
    return Theme({
        "markdown.paragraph": "", "markdown.text": "",
        "markdown.h1": f"bold {p['sumi']}", "markdown.h1.border": deep,
        "markdown.h2": f"bold {p['sumi']}",
        "markdown.h3": f"bold {p['ai']}", "markdown.h4": p["ai"],
        "markdown.h5": f"italic {p['ash']}", "markdown.h6": p["ash"],
        "markdown.strong": f"bold {p['sumi']}", "markdown.em": "italic",
        "markdown.code": f"{p['ai']} on {deep}", "markdown.code_block": f"on {deep}",
        "markdown.block_quote": f"italic {p['ash']}",
        "markdown.list": p["sumi"], "markdown.item.bullet": f"bold {p['vermilion']}",
        "markdown.item.number": p["vermilion"],
        "markdown.hr": p["ash"],
        # With hyperlinks on, Rich styles link text as link_url: both are the reader's links
        "markdown.link": f"underline {p['ai']}", "markdown.link_url": f"underline {p['ai']}",
        "markdown.table.border": p["ash"], "markdown.table.header": f"bold {p['sumi']}",
    }, inherit=True)


class _Linker:
    """Turns the `vs:<n>` links Rich left in the segments into click actions."""

    def __init__(self, targets: list[Target], note: Path | None, files: VaultFiles | None,
                 palette: dict[str, str], width: int):
        self.targets, self.note, self.files, self.width = targets, note, files, width
        self.missing = Style(color=palette["ash"], underline=False, strike=True)
        self.links: list[tuple[int, int, int, int]] = []

    def _index(self, href: str) -> int:
        if href.startswith("vs:") and href[3:].isdigit():
            return int(href[3:])
        self.targets.append(_href_target(href, self.note, self.files))
        return len(self.targets) - 1

    def _click(self, i: int) -> Style:
        return Style.from_meta({"@click": f"follow({i})"})

    def lines(self, rendered: list[list[Segment]], start: int) -> list[Strip]:
        """`rendered` (lines that will begin at line `start` of the page) with live links."""
        out: list[Strip] = []
        for segments in rendered:
            x, new = 0, []
            for seg in segments:
                style = seg.style
                if style is not None and style.link:
                    i = self._index(style.link)
                    style = style.update_link(None) + self._click(i)
                    if self.targets[i].kind == "missing":
                        style += self.missing
                    line = start + len(out)
                    if self.links and self.links[-1][0] == line and self.links[-1][3] == i \
                            and self.links[-1][1] + self.links[-1][2] == x:
                        self.links[-1] = (line, self.links[-1][1], self.links[-1][2] + seg.cell_length, i)
                    else:
                        self.links.append((line, x, seg.cell_length, i))
                new.append(Segment(seg.text, style, seg.control))
                x += seg.cell_length
            out.append(Strip(new, self.width))
        return out


def render(text: str, width: int, palette: dict[str, str], dark: bool,
           targets: list[Target] | None = None, note: Path | None = None, files: VaultFiles | None = None) -> Page:
    """Markdown to a Page `width` cells wide, painted on the palette's paper. Thread-safe.

    `targets` are the links `prepare` made; `note` and `files` resolve any other relative link.
    """
    width = max(width, 20)
    base = Style(color=palette["sumi"], bgcolor=palette["washi"])
    console = Console(
        file=io.StringIO(), width=width, color_system="truecolor", force_terminal=True,
        legacy_windows=False, theme=rich_theme(palette),
    )
    options = console.options.update_width(width)
    code_theme = "native" if dark else "friendly"
    blank = Strip.blank(width, base)
    linker = _Linker(list(targets or []), note, files, palette, width)
    lines: list[Strip] = []
    headings: list[tuple[int, str]] = []
    for heading, source in split_sections(text):
        if lines:
            lines.append(blank)  # the gap Rich leaves between blocks, kept between sections
        if heading is not None:
            headings.append((len(lines), heading))
        md = _NoteMarkdown(source, code_theme=code_theme, hyperlinks=True)
        lines.extend(linker.lines(console.render_lines(md, options, style=base, pad=True), len(lines)))
    return Page(lines or [blank], headings, width, linker.targets, linker.links)


def render_note(path: Path, width: int, palette: dict[str, str], dark: bool, files: VaultFiles | None) -> Page:
    text, targets = prepare(path.read_text(encoding="utf-8", errors="replace"), path, files)
    return render(text, width, palette, dark, targets, path, files)


class RenderCache:
    """The last few rendered notes, keyed so a stale entry can never be shown.

    A key holds the file's mtime and size (edited notes render again), the width (a resized
    reader renders again) and the theme. Shared by the reader and the prefetch thread.
    """

    def __init__(self, size: int = 40):
        self.size = size
        self._pages: OrderedDict[tuple, Page] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(path: Path, width: int, theme: str) -> tuple | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (str(path), st.st_mtime_ns, st.st_size, width, theme)

    def get(self, key: tuple | None) -> Page | None:
        if key is None:
            return None
        with self._lock:
            page = self._pages.get(key)
            if page is not None:
                self._pages.move_to_end(key)
            return page

    def put(self, key: tuple | None, page: Page) -> None:
        if key is None:
            return
        with self._lock:
            self._pages[key] = page
            self._pages.move_to_end(key)
            while len(self._pages) > self.size:
                self._pages.popitem(last=False)

    def load(self, path: Path, width: int, theme: str, palette: dict[str, str], dark: bool,
             files: VaultFiles | None = None) -> Page:
        """The note at `path` as a Page, rendering it only if it isn't cached."""
        key = self.key(path, width, theme)
        if (page := self.get(key)) is not None:
            return page
        try:
            page = render_note(path, width, palette, dark, files)
        except OSError as e:
            return render(f"_Could not read {path.name}: {e}_", width, palette, dark)  # not cached: retry next time
        self.put(key, page)
        return page

    def __contains__(self, key: tuple | None) -> bool:
        with self._lock:
            return key in self._pages
