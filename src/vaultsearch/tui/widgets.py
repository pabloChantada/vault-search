"""List items shown in the results panel."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import ListItem, Static

from ..theme import ACCENT, DIM, PATH


class ResultItem(ListItem):
    """A search hit (or a recent note): path › heading, a snippet and its tags."""

    def __init__(self, index: int, path: str, heading: str = "", body: Text | None = None, meta: str = ""):
        super().__init__()
        self.path = path
        self.heading = heading
        self._index = index
        self._body = body
        self._meta = meta

    def compose(self) -> ComposeResult:
        head = Text()
        head.append(f"{self._index:>2}  ", style=f"bold {ACCENT}")
        head.append(self.path, style=f"bold {PATH}")
        if self.heading:
            head.append(f"  › {self.heading}", style=DIM)
        yield Static(head, classes="r-head")
        if self._body:
            yield Static(self._body, classes="r-body")
        if self._meta:
            yield Static(self._meta, classes="r-meta")


class CommandItem(ListItem):
    """A slash command suggestion."""

    def __init__(self, command: str, description: str):
        super().__init__()
        self.command = command
        self._description = description

    def compose(self) -> ComposeResult:
        t = Text()
        t.append(f"{self.command:<10}", style=f"bold {ACCENT}")
        t.append(self._description, style=DIM)
        yield Static(t)
