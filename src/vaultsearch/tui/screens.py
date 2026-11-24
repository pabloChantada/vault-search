"""Pop-up screens: the shortcut sheet and the activity log."""
from __future__ import annotations

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static

from .help import SHORTCUTS


def _sheet_section(title: str, keys: list[tuple[str, str]]) -> Content:
    """A titled block of `keys  what they do` rows, keys aligned in a column."""
    width = max(len(k) for k, _ in keys) + 3
    parts: list[tuple[str, str] | str] = [(title, "bold $ai")]
    for k, what in keys:
        parts += ["\n", (k.ljust(width), "bold $seal"), what]
    return Content.assemble(*parts)


class ShortcutsScreen(ModalScreen[None]):
    """Every key on one sheet. Any of ? f1 esc q closes it."""

    BINDINGS = [Binding("escape,question_mark,f1,q", "dismiss", "Close", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="sheet"):
            yield Static("[b]Shortcuts[/]", classes="sheet-title")
            with Grid(id="sheet-grid"):
                for section, keys in SHORTCUTS:
                    yield Static(_sheet_section(section, keys), classes="sheet-section")
            yield Static("[$ash]esc to close[/]", classes="sheet-hint")

    def on_click(self) -> None:
        self.dismiss()


class ActivityScreen(ModalScreen[None]):
    """What the indexer and the watcher have done this session."""

    BINDINGS = [Binding("escape,q", "dismiss", "Close", show=False)]

    def __init__(self, lines: Iterable[str]):
        super().__init__()
        self._lines = list(lines)

    def compose(self) -> ComposeResult:
        with Vertical(id="sheet", classes="activity"):
            yield Static("[b]Activity[/]", classes="sheet-title")
            yield RichLog(id="activity-log", wrap=True, markup=False)
            yield Static("[$ash]esc to close[/]", classes="sheet-hint")

    def on_mount(self) -> None:
        log = self.query_one(RichLog)
        for line in self._lines or ["Nothing yet."]:
            log.write(line)
