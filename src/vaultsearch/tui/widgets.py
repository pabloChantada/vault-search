"""The results list items and the note reader."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path, PurePosixPath

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.geometry import Size
from textual.markup import escape
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.timer import Timer
from textual.widgets import ListItem, ListView, Static
from textual.worker import get_current_worker

from ..core.links import VaultFiles
from ..theme import NIGHT, PAPER
from . import images
from .reader import Page, RenderCache, render

HINT_KEYS = "asdfghjklqwertyuiopzxcvbnm"  # labels for links on screen in hint mode, home row first


def open_with_system(path: Path) -> None:
    """Open a file in the app the OS associates with it (an image viewer for images)."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a file from the user's own vault
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


class NoteView(ScrollView, can_focus=True):
    """The note reader. Moves like a pager in Vim; the mouse wheel works too.

    It paints a Page of pre-rendered lines (see tui/reader.py) and only the lines on screen,
    so a long note costs no more to show than a short one. Notes already in the cache show at
    once; others render in a thread after a short pause, so holding `j` in the results doesn't
    render every note it passes.

    Links work with the mouse (a click runs `follow`) and the keyboard: `f` labels the links
    on screen with a letter, and pressing it follows that link. Notes reached by a link stack
    up, and backspace walks back through them.
    """

    DEBOUNCE_S = 0.06

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
        Binding("f", "hints", "Follow a link", show=False),
        Binding("backspace", "back", "Back", show=False),
    ]

    def __init__(self, *, id: str | None = None):
        super().__init__(id=id)
        self.cache = RenderCache()
        self.files: VaultFiles | None = None  # set by the app: resolves [[links]] and ![[images]]
        self.page = Page([], [], 0)
        self._source: tuple = ("text", "", "")  # ("text", markdown, "") or ("file", path, heading)
        self._shown: tuple | None = None  # the source of the page on screen; lags _source while rendering
        self._timer: Timer | None = None
        self._history: list[tuple[Path, int]] = []  # notes left by following a link: (path, line)
        self._hints: dict[str, tuple[int, int, int]] | None = None  # key -> (line, x, target)

    def on_mount(self) -> None:
        self.watch(self.app, "theme", lambda: self._rerender(keep_scroll=True), init=False)

    @property
    def ready(self) -> bool:
        """Whether the page on screen is the one last asked for (and not still rendering)."""
        return self._shown == self._source

    # ---------- what to show ----------
    def _look(self) -> tuple[int, str, dict[str, str], bool]:
        width = self.scrollable_content_region.width or 80  # 0 until the first layout
        dark = self.app.current_theme.dark
        return width, self.app.theme, NIGHT if dark else PAPER, dark

    def show_text(self, markdown: str) -> None:
        """Show a short message (the welcome text, "nothing matches"). Rendered right here."""
        self._cancel_pending()
        self._source = ("text", markdown, "")
        width, _, palette, dark = self._look()
        self._set_page(render(markdown, width, palette, dark))
        self._shown = self._source

    def show_file(self, path: Path, heading: str = "", keep_scroll: bool = False,
                  line: int | None = None, keep_history: bool = False) -> None:
        """Show the note at `path`, scrolled to the section `heading` ("A > B") if given, or to
        `line`. Showing a note from outside (a new result) forgets the trail of followed links."""
        self._cancel_pending()
        if not (keep_history or keep_scroll):
            self._history.clear()
        self._source = ("file", path, heading)
        width, theme, palette, dark = self._look()
        page = self.cache.get(self.cache.key(path, width, theme))
        if page is not None:
            self._set_page(page, heading, keep_scroll, line)
            self._shown = self._source
            return
        source = self._source
        self._timer = self.set_timer(
            self.DEBOUNCE_S,
            lambda: self.run_worker(
                lambda: self._render_in_thread(source, width, theme, palette, dark, keep_scroll, line),
                thread=True, exclusive=True, group="note-render",
            ),
        )

    def prefetch(self, paths: list[Path]) -> None:
        """Render these notes ahead of time, nearest first, so moving onto them is instant."""
        width, theme, palette, dark = self._look()

        def run() -> None:
            worker = get_current_worker()
            for p in paths:
                if worker.is_cancelled:
                    return  # the selection moved on: a newer prefetch has the right neighbours
                self.cache.load(p, width, theme, palette, dark, self.files)

        self.run_worker(run, thread=True, exclusive=True, group="note-prefetch")

    def _render_in_thread(self, source: tuple, width: int, theme: str, palette: dict, dark: bool,
                          keep: bool, line: int | None) -> None:
        page = self.cache.load(source[1], width, theme, palette, dark, self.files)
        self.app.call_from_thread(self._arrive, source, page, keep, line)

    def _arrive(self, source: tuple, page: Page, keep_scroll: bool, line: int | None) -> None:
        if source == self._source:  # else the selection moved while this rendered
            self._set_page(page, source[2], keep_scroll, line)
            self._shown = source

    def _cancel_pending(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _set_page(self, page: Page, heading: str = "", keep_scroll: bool = False, line: int | None = None) -> None:
        fraction = self.scroll_y / max(1, len(self.page.lines)) if keep_scroll else 0.0
        self._hints = None
        self.page = page
        # Width 0: lines are already wrapped, and a ScrollView sizes itself to its virtual width,
        # so the page's own width would widen the reader and the next page would wrap wider still
        self.virtual_size = Size(0, len(page.lines))
        self.refresh()
        if keep_scroll:
            y = round(fraction * len(page.lines))
        elif line is not None:
            y = line
        else:
            y = self._section_line(heading.split(" > ")[-1].strip()) if heading else 0
        self.scroll_to(y=y, animate=False, immediate=True)
        if round(self.scroll_y) != y:  # a taller page isn't laid out yet: the scroll was clamped
            self.call_after_refresh(lambda: self.page is page and self.scroll_to(y=y, animate=False, immediate=True))

    def _rerender(self, keep_scroll: bool) -> None:
        kind, what, heading = self._source
        if kind == "file":
            self.show_file(what, heading, keep_scroll=keep_scroll)
        else:
            self.show_text(what)

    def on_resize(self) -> None:
        if self.page.width and self._look()[0] != self.page.width:
            self._rerender(keep_scroll=True)  # lines are wrapped to a width: wrap them again

    # ---------- painting ----------
    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        i = scroll_y + y
        width = self.scrollable_content_region.width
        lines = self.page.lines
        strip = lines[i] if 0 <= i < len(lines) else Strip.blank(width, self.rich_style)
        if self._hints:
            strip = self._with_hints(strip, i)
        return strip.crop_extend(scroll_x, scroll_x + width, self.rich_style)

    def _with_hints(self, strip: Strip, line: int) -> Strip:
        """Stamp each hint letter over the first cell of its link, like a seal."""
        palette = self._look()[2]
        seal = Style(bold=True, color=palette["washi"], bgcolor=palette["vermilion"])
        for key, (at, x, _) in self._hints.items():
            if at == line:
                strip = Strip.join([strip.crop(0, x), Strip([Segment(key, seal)], 1), strip.crop(x + 1)])
        return strip

    # ---------- links ----------
    def action_follow(self, index: int) -> None:
        """Follow link `index` of the page: a note opens here, anything else outside the app."""
        if not 0 <= index < len(self.page.targets):
            return
        t = self.page.targets[index]
        if t.kind == "note" and t.path is not None:
            kind, here, _ = self._source
            if kind == "file":
                self._history.append((here, round(self.scroll_y)))
            if kind == "file" and t.path == here:
                if not self.scroll_to_section(t.heading):  # [[#Heading]]: same note, other section
                    self._history.pop()
                return
            self.show_file(t.path, t.heading, keep_history=True)
        elif t.kind == "missing":
            self.notify(f"There is no note called “{t.label}” yet", severity="warning", timeout=3)
        elif t.kind == "url":
            webbrowser.open(t.url)
        elif t.kind == "image":
            if t.path is None:
                self.notify(f"{t.label.lstrip('▣ ')} isn't in the vault", severity="warning", timeout=3)
            elif images.supported():
                # Every image of the note, in order, so ← → can walk through them
                gallery = list(dict.fromkeys(x.path for x in self.page.targets if x.kind == "image" and x.path))
                self.app.push_screen(images.ImageScreen(gallery, gallery.index(t.path)))
            else:
                open_with_system(t.path)  # the terminal can't draw pixels

    def action_back(self) -> None:
        """Back to the note this one was reached from, where it was left."""
        if self._history:
            path, line = self._history.pop()
            self.show_file(path, line=line, keep_history=True)

    def action_hints(self) -> None:
        """Label every link on screen with a key; the next key press follows that link."""
        top, bottom = round(self.scroll_y), round(self.scroll_y) + self.scrollable_content_region.height
        visible = [(line, x, i) for line, x, _, i in self.page.links if top <= line < bottom]
        if not visible:
            self.notify("No links on screen", timeout=2)
            return
        self._hints = dict(zip(HINT_KEYS, visible))
        self.refresh()

    def on_key(self, event: events.Key) -> None:
        if self._hints is None:
            return
        event.stop()
        event.prevent_default()  # in hint mode every key picks a link (or cancels)
        hints, self._hints = self._hints, None
        self.refresh()
        if event.character in hints:
            self.action_follow(hints[event.character][2])

    # ---------- moving ----------
    def action_scroll_home(self) -> None:  # g: jump, as in Vim (Textual's default glides there)
        self.scroll_to(y=0, animate=False, immediate=True)

    def action_scroll_end(self) -> None:  # G
        self.scroll_to(y=self.max_scroll_y, animate=False, immediate=True)

    def action_half_page(self, direction: int) -> None:
        self.scroll_relative(y=direction * max(1, self.scrollable_content_region.height // 2), animate=False)

    def action_heading(self, direction: int) -> None:
        """Jump to the next (1) or previous (-1) heading below or above the top line."""
        top = self.scroll_y
        offsets = [line for line, _ in self.page.headings]
        if direction > 0:
            target = next((y for y in offsets if y > top + 0.5), None)
        else:
            target = next((y for y in reversed(offsets) if y < top - 0.5), None)
        if target is not None:
            self.scroll_to(y=target, animate=False, immediate=True)

    def _section_line(self, section: str) -> int:
        """The line of the first heading that contains `section`, or 0 (the top) if none does."""
        return next((line for line, text in self.page.headings if section and section in text), 0)

    def scroll_to_section(self, section: str) -> bool:
        """Put the first heading that contains `section` at the top. False if there is none."""
        line = self._section_line(section)
        if not line and not any(section and section in text for _, text in self.page.headings):
            return False
        self.scroll_to(y=line, animate=False, immediate=True)
        return True
