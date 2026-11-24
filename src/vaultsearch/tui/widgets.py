"""The results list items and the note reader."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.markup import escape
from textual.widgets import ListItem, ListView, Markdown, Static

_HEADINGS = "MarkdownH1, MarkdownH2, MarkdownH3, MarkdownH4, MarkdownH5, MarkdownH6"


def mark_words(text: str, words: list[str]) -> str:
    """Escape `text` as Textual markup and set the query words in the seal colour."""
    words = [w for w in words if w]
    if not words:
        return escape(text)
    pattern = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.IGNORECASE)
    out, last = [], 0
    for m in pattern.finditer(text):
        out.append(escape(text[last:m.start()]))
        out.append(f"[b $seal]{escape(m.group())}[/]")
        last = m.end()
    out.append(escape(text[last:]))
    return "".join(out)


class ResultItem(ListItem):
    """A note in the results: its title, where it lives, and a line of what matched."""

    def __init__(self, path: str, heading: str = "", body: str = ""):
        super().__init__()
        self.path = path
        self.heading = heading
        self._body = body  # already markup (see mark_words)

    def compose(self) -> ComposeResult:
        p = PurePosixPath(self.path)
        where = str(p.parent) if str(p.parent) != "." else ""
        if self.heading:
            heading = self.heading.replace(" > ", " › ")
            where = f"{where} › {heading}" if where else heading
        head = f"[b $ink]{escape(p.stem)}[/]"
        if where:
            head += f"  [$ai]{escape(where)}[/]"
        yield Static(head, classes="r-head")
        if self._body:
            yield Static(self._body, classes="r-body")


class CommandItem(ListItem):
    """A slash command suggestion."""

    def __init__(self, command: str, description: str):
        super().__init__()
        self.command = command
        self._description = description

    def compose(self) -> ComposeResult:
        yield Static(f"[b $seal]{self.command:<10}[/][$ash]{escape(self._description)}[/]")


class ResultList(ListView):
    """The results, with j/k as well as the arrows."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "first", "First", show=False),
        Binding("G", "last", "Last", show=False),
        Binding("l,right,tab", "app.focus_note", "Read", show=False),
        Binding("escape", "app.focus_prompt", "Search", show=False),
    ]

    def action_first(self) -> None:
        if self.children:
            self.index = 0

    def action_last(self) -> None:
        if self.children:
            self.index = len(self.children) - 1


class NoteView(VerticalScroll, can_focus=True):
    """The note reader. Moves like a pager in Vim; the mouse wheel works too."""

    BINDINGS = [
        Binding("j,down", "scroll_down", "Down", show=False),
        Binding("k,up", "scroll_up", "Up", show=False),
        Binding("ctrl+d", "half_page(1)", "Half page down", show=False),
        Binding("ctrl+u", "half_page(-1)", "Half page up", show=False),
        Binding("space,ctrl+f,pagedown", "page_down", "Page down", show=False),
        Binding("b,ctrl+b,pageup", "page_up", "Page up", show=False),
        Binding("g,home", "scroll_home", "Top", show=False),
        Binding("G,end", "scroll_end", "Bottom", show=False),
        Binding("right_square_bracket", "heading(1)", "Next heading", show=False),
        Binding("left_square_bracket", "heading(-1)", "Previous heading", show=False),
        Binding("h,left,escape,shift+tab", "app.focus_results", "Results", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Markdown(id="preview")

    def action_half_page(self, direction: int) -> None:
        self.scroll_relative(y=direction * max(1, self.scrollable_content_region.height // 2), animate=False)

    def action_heading(self, direction: int) -> None:
        """Jump to the next (1) or previous (-1) heading below or above the top line."""
        top = self.scroll_y
        base = self.query_one(Markdown).virtual_region.y  # headings are placed inside the Markdown
        offsets = sorted(base + h.virtual_region.y for h in self.query(_HEADINGS))
        if direction > 0:
            target = next((y for y in offsets if y > top + 0.5), None)
        else:
            target = next((y for y in reversed(offsets) if y < top - 0.5), None)
        if target is not None:
            self.scroll_to(y=target, animate=False)

    def scroll_to_section(self, section: str) -> None:
        for block in self.query(_HEADINGS):
            if section in (getattr(block, "source", None) or ""):
                self.scroll_to_widget(block, top=True, animate=False)
                return
