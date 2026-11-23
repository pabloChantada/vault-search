"""The interactive app: search as you type, read the note, open it in Obsidian or on the map."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import webbrowser
from collections import deque
from collections.abc import Iterable
from datetime import datetime
from urllib.parse import quote

from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Key
from textual.markup import escape
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.timer import Timer
from textual.widgets import Input, ListItem, Markdown, Static

from ..core import Config, Engine, Hit
from ..core.text import preview_markdown, query_words, snippet
from .help import COMMANDS, WELCOME_MD
from .screens import ActivityScreen, ShortcutsScreen
from .themes import NIGHT_THEME, PAPER_THEME, THEMES
from .widgets import CommandItem, NoteView, ResultItem, ResultList, mark_words

# Keys that are also letters: they must type into the search box, not act, while it has focus
_LETTER_ACTIONS = {"open_selected", "copy_path", "map", "shortcuts"}
_EVENT_SHOWN_S = 8.0  # how long the latest watcher event stays in the status line


class _PanelLogHandler(logging.Handler):
    """Routes engine logs (indexer/watcher, other threads) into the app."""

    def __init__(self, app: "VaultSearchApp"):
        super().__init__(logging.INFO)
        self.app = app

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.app.call_from_thread(self.app.log_event, record.levelno, record.getMessage())
        except Exception:
            pass  # app shutting down


class VaultSearchApp(App):
    TITLE = "vaultsearch"
    CSS_PATH = "styles.tcss"
    COMMAND_PALETTE_BINDING = "ctrl+p"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("slash", "focus_prompt", "Search", show=False),
        Binding("escape", "focus_prompt", "Search", show=False),
        Binding("question_mark", "shortcuts(True)", "Shortcuts", show=False),
        Binding("f1", "shortcuts", "Shortcuts", show=False),
        Binding("m", "map", "Map", show=False),
        Binding("o", "open_selected('obsidian')", "Open in Obsidian", show=False),
        Binding("e", "open_selected('editor')", "Open in editor", show=False),
        Binding("y", "copy_path", "Copy path", show=False),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.engine = Engine(cfg)
        self.state = "loading the model"
        self.progress = ""
        self.folder: str | None = None
        self.tag: str | None = None
        self.summary = ""  # "12 results, 8 ms"
        self.events: deque[str] = deque(maxlen=300)
        self._last_event: tuple[float, str, bool] | None = None  # (when, text, is_problem)
        self._debounce: Timer | None = None
        self._last_query = ""
        self._map_busy = False
        self._focus_when_found = False
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = PAPER_THEME.name

    # ---------- layout ----------
    def compose(self) -> ComposeResult:
        with Horizontal(id="brand"):
            yield Static(f"[b]vaultsearch[/]  [$ash]{escape(self.cfg.vault.name)}[/]", id="title")
            yield Static(" 印 ", id="seal", classes="busy")
        yield Input(
            placeholder="Search your vault",
            id="prompt",
            suggester=SuggestFromList(list(COMMANDS), case_sensitive=False),
        )
        yield Static(id="scope")
        with Horizontal(id="main"):
            yield ResultList(id="results")
            yield NoteView(id="note")
        with Horizontal(id="statusbar"):
            yield Static(id="status")
            yield Static("[b $seal]?[/] [$ash]shortcuts[/]  [b $seal]ctrl+p[/] [$ash]commands[/]", id="keys")

    def on_mount(self) -> None:
        engine_log = logging.getLogger("vaultsearch")
        engine_log.handlers[:] = [_PanelLogHandler(self)]
        engine_log.setLevel(logging.INFO)
        engine_log.propagate = False
        self.query_one("#preview", Markdown).update(WELCOME_MD)
        self.query_one("#prompt", Input).focus()
        self._refresh_scope()
        self._refresh_status()
        self.set_interval(1.0, self._refresh_status)
        self.boot()

    def on_unmount(self) -> None:
        self.engine.stop()
        engine_log = logging.getLogger("vaultsearch")
        engine_log.handlers[:] = []
        engine_log.propagate = True

    # ---------- startup: model, watcher, first sync (background thread) ----------
    @work(thread=True, exclusive=True, group="boot")
    def boot(self) -> None:
        try:
            self.engine.open()
        except Exception as e:  # e.g. the model download failed
            self.state = f"error: {e}"
            self.call_from_thread(self.log_event, logging.ERROR, f"Startup failed: {e}")
            return
        self.engine.start_watching()
        self.state = "indexing"
        t0 = time.perf_counter()
        counts = self.engine.indexer.sync_all(on_progress=self._on_progress)
        self.progress = ""
        self.state = "ready"
        self.call_from_thread(self.log_event, logging.INFO,
                              f"Initial sync: {_describe(counts)} ({time.perf_counter() - t0:.1f}s)")
        if self._last_query:
            self.call_from_thread(self.run_search, self._last_query)

    def _on_progress(self, done: int, total: int, rel: str, status: str) -> None:
        self.progress = f"{done}/{total}"

    # ---------- status line ----------
    def _refresh_status(self) -> None:
        seal = self.query_one("#seal", Static)
        seal.set_class(self.state != "ready", "busy")
        if self.state == "ready":
            left = escape(self.summary) if self.summary else self._note_count()
        elif self.state.startswith("error"):
            left = f"[b $seal]{escape(self.state)}[/]"
        else:
            left = f"[$ash]{escape(self.state)}{' ' + self.progress if self.progress else ''}…[/]"
        if self._last_event and time.monotonic() - self._last_event[0] < _EVENT_SHOWN_S:
            _, text, problem = self._last_event
            left += f"   [{'$seal' if problem else '$moss'}]{escape(text)}[/]"
        self.query_one("#status", Static).update(left)

    def _note_count(self) -> str:
        if not self.engine.store:
            return ""
        return f"[$ash]{self.engine.store.stats()['notes']:,} notes, kept up to date[/]"

    def _refresh_scope(self) -> None:
        parts = [f"in [b]{escape(self.folder)}/[/]"] if self.folder else []
        parts += [f"tagged [b]#{escape(self.tag)}[/]"] if self.tag else []
        scope = self.query_one("#scope", Static)
        scope.display = bool(parts)
        scope.update("[$ash]Only notes[/] " + " and ".join(parts) + "   [$ash]/folder off  /tag off[/]")

    def log_event(self, level: int, msg: str) -> None:
        self.events.append(f"{datetime.now():%H:%M:%S}  {msg}")
        self._last_event = (time.monotonic(), msg, level >= logging.WARNING)
        self._refresh_status()

    # ---------- the command palette (ctrl+p): only what this app does ----------
    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield SystemCommand("Recent notes", "The notes you edited last", lambda: self.run_command("/recent"))
        yield SystemCommand("Search one folder", "Type the folder after /folder", lambda: self._ask("/folder "))
        yield SystemCommand("Search one tag", "Type the tag after /tag", lambda: self._ask("/tag "))
        if self.folder or self.tag:
            yield SystemCommand("Search everything", "Clear the folder and tag", self.clear_scope)
        yield SystemCommand("Open the 3D map", "In a window of its own", self.action_map)
        night = self.theme == NIGHT_THEME.name
        yield SystemCommand("Paper colours" if night else "Night colours",
                            "Ink on paper" if night else "Paper on ink", self.action_toggle_theme)
        yield SystemCommand("Activity", "What the indexer and the watcher did", self.action_activity)
        yield SystemCommand("Rescan the vault", "Index every note again", self.reindex)
        yield SystemCommand("Shortcuts", "Every key", self.action_shortcuts)
        yield SystemCommand("Quit", "Close vaultsearch", self.exit)

    def _ask(self, prefix: str) -> None:
        prompt = self.query_one("#prompt", Input)
        prompt.value = prefix
        prompt.cursor_position = len(prefix)
        prompt.focus()

    def action_toggle_theme(self) -> None:
        self.theme = PAPER_THEME.name if self.theme == NIGHT_THEME.name else NIGHT_THEME.name

    def action_shortcuts(self, from_letter: bool = False) -> None:
        if not isinstance(self.screen, ShortcutsScreen):
            self.push_screen(ShortcutsScreen())

    def action_activity(self) -> None:
        self.push_screen(ActivityScreen(self.events))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if not isinstance(self.focused, Input):
            return True
        # 'o', 'm', 'y', '?' are letters while typing a search; f1 is not
        return action not in _LETTER_ACTIONS or (action == "shortcuts" and not parameters)

    # ---------- focus ----------
    def on_key(self, event: Key) -> None:
        if event.key == "down" and self.focused is self.query_one("#prompt", Input):
            self.action_focus_results()
            event.stop()

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt", Input).focus()

    def action_focus_results(self) -> None:
        results = self.query_one("#results", ResultList)
        if results.children:
            results.focus()
            if results.index is None:
                results.index = 0

    def action_focus_note(self) -> None:
        self.query_one("#note", NoteView).focus()

    # ---------- search ----------
    @on(Input.Changed, "#prompt")
    def _on_prompt_changed(self, event: Input.Changed) -> None:
        q = event.value.strip()
        if q.startswith("/"):
            self._show_command_hints(q)
            return
        if self._debounce:
            self._debounce.stop()
        if q:
            self._debounce = self.set_timer(0.25, lambda: self.run_search(q))

    @on(Input.Submitted, "#prompt")
    def _on_prompt_submitted(self, event: Input.Submitted) -> None:
        q = event.value.strip()
        if q.startswith("/"):
            event.input.value = ""
            self.run_command(q)
            return
        if q:
            self._focus_when_found = True  # enter before the results arrive still lands on them
            self.run_search(q)
        self.action_focus_results()

    @work(thread=True, exclusive=True, group="search")
    def run_search(self, query: str) -> None:
        self._last_query = query
        if not self.engine.ready.wait(0.05):
            self.summary = "waiting for the model…"
            return  # boot() re-runs the last query once it's ready
        t0 = time.perf_counter()
        hits = self.engine.searcher.search(query, limit=30, folder=self.folder, tag=self.tag)
        self.call_from_thread(self._show_hits, query, hits, (time.perf_counter() - t0) * 1000)

    def _show_hits(self, query: str, hits: list[Hit], ms: float) -> None:
        if query != self._last_query:
            return  # a newer query is on its way
        words = query_words(query)
        items: list[ListItem] = []
        for h in hits:
            heading = h.heading if h.heading != h.title else ""
            items.append(ResultItem(h.path, heading, mark_words(snippet(h.text, words, 160), words)))
        self._fill_results(items, f"{len(hits)} result{'s' * (len(hits) != 1)}, {ms:.0f} ms")
        if not hits:
            self.query_one("#preview", Markdown).update(
                f"Nothing matches **{query}**. Try other words, or `/folder off` if a folder filter is on.")

    def _fill_results(self, items: list[ListItem], summary: str) -> None:
        results = self.query_one("#results", ResultList)
        results.clear()
        if items:
            results.extend(items)
            results.index = 0
            if isinstance(items[0], ResultItem):
                self.load_preview(items[0].path, items[0].heading)
                if self._focus_when_found:
                    results.focus()
        self._focus_when_found = False
        self.summary = summary
        self._refresh_status()

    # ---------- the note ----------
    @on(ResultList.Highlighted, "#results")
    def _on_highlighted(self, event: ResultList.Highlighted) -> None:
        if isinstance(event.item, ResultItem):
            self.load_preview(event.item.path, event.item.heading)

    @work(exclusive=True, group="preview")
    async def load_preview(self, rel: str, heading: str) -> None:
        try:
            text = preview_markdown((self.cfg.vault / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            text = f"_Could not read {rel}: {e}_"
        note = self.query_one("#note", NoteView)
        await self.query_one("#preview", Markdown).update(text)
        note.scroll_home(animate=False)
        section = heading.split(" > ")[-1].strip() if heading else ""
        if section:  # jump to the section the hit came from
            note.scroll_to_section(section)

    # ---------- opening notes ----------
    def _selected_path(self) -> str | None:
        item = self.query_one("#results", ResultList).highlighted_child
        return item.path if isinstance(item, ResultItem) else None

    @on(ResultList.Selected, "#results")
    def _on_selected(self, event: ResultList.Selected) -> None:
        if isinstance(event.item, ResultItem):
            self.open_in_obsidian(event.item.path)
        elif isinstance(event.item, CommandItem):
            self.query_one("#prompt", Input).value = ""
            self.run_command(event.item.command)

    def action_open_selected(self, how: str) -> None:
        rel = self._selected_path()
        if rel:
            self.open_in_obsidian(rel) if how == "obsidian" else self.open_in_editor(rel)

    def open_in_obsidian(self, rel: str) -> None:
        file = rel[:-3] if rel.lower().endswith(".md") else rel
        webbrowser.open(f"obsidian://open?vault={quote(self.cfg.vault.name)}&file={quote(file)}")
        self.notify(f"Opening {rel} in Obsidian", timeout=2)

    def open_in_editor(self, rel: str) -> None:
        path = str(self.cfg.vault / rel)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            self.notify(f"Opening {rel}", timeout=2)
        except Exception as e:
            self.notify(f"Could not open {rel}: {e}", severity="error")

    def action_copy_path(self) -> None:
        rel = self._selected_path()
        if rel:
            self.copy_to_clipboard(str(self.cfg.vault / rel))
            self.notify("Path copied", timeout=2)

    # ---------- the 3D map ----------
    def action_map(self) -> None:
        if self._map_busy:
            self.notify("The map is still being drawn", timeout=2)
            return
        self._map_busy = True
        self.notify("Drawing the map…", timeout=3)
        self.open_map()

    @work(thread=True, exclusive=True, group="map")
    def open_map(self) -> None:
        from ..viz import build_graph, map_page, open_window, window_profile, write_html

        try:
            if not self.engine.ready.wait(60):
                self.call_from_thread(self.notify, "The index is not ready yet", severity="warning")
                return
            graph = build_graph(self.engine.store)
            page = write_html(graph, map_page(self.cfg.db_path), self.cfg.vault.name)
            how = open_window(page, window_profile(self.cfg.db_path))
            where = "in its own window" if how == "window" else "in your browser"
            self.call_from_thread(self.notify, f"Map of {len(graph['nodes'])} notes opened {where}", timeout=4)
        except Exception as e:
            self.call_from_thread(self.notify, f"Could not draw the map: {e}", severity="error")
        finally:
            self._map_busy = False

    # ---------- slash commands ----------
    def _show_command_hints(self, q: str) -> None:
        typed = q.split()[0].lower()
        items = [CommandItem(c, d) for c, d in COMMANDS.items() if c.startswith(typed)]
        self._fill_results(items, "enter runs the command, → completes it")

    def clear_scope(self) -> None:
        self.folder = self.tag = None
        self._scope_changed()

    def _scope_changed(self) -> None:
        self._refresh_scope()
        if self._last_query:
            self.run_search(self._last_query)

    def run_command(self, raw: str) -> None:
        parts = raw.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        if cmd == "/quit":
            self.exit()
        elif cmd == "/recent":
            self.show_recent(int(arg) if arg.isdigit() else 30)
        elif cmd == "/folder":
            self.folder = None if arg in ("", "off") else arg.strip("/")
            self._scope_changed()
        elif cmd == "/tag":
            self.tag = None if arg in ("", "off") else arg.lstrip("#")
            self._scope_changed()
        elif cmd == "/map":
            self.action_map()
        elif cmd == "/reindex":
            self.reindex()
        elif cmd in ("/help", "/keys"):
            self.action_shortcuts()
        else:
            self.notify(f"There is no {cmd} command. Press ctrl+p to see them all", severity="warning")

    @work(thread=True, group="recent")
    def show_recent(self, n: int) -> None:
        if not self.engine.ready.wait(30):
            return
        rows = self.engine.store.list_files(self.folder, n)

        def build() -> None:
            items: list[ListItem] = []
            for r in rows:
                when = datetime.fromtimestamp(r["mtime"]).strftime("edited %d %b, %H:%M")
                items.append(ResultItem(r["path"], "", f"[$ash]{when}[/]"))
            self._fill_results(items, f"{len(rows)} recent notes")
            self.action_focus_results()

        self.call_from_thread(build)

    @work(thread=True, exclusive=True, group="reindex")
    def reindex(self) -> None:
        if not self.engine.ready.wait(30):
            return
        self.state = "rescanning"
        counts = self.engine.indexer.sync_all(on_progress=self._on_progress)
        self.progress = ""
        self.state = "ready"
        self.call_from_thread(self.log_event, logging.INFO, f"Rescan: {_describe(counts)}")


def _describe(counts: dict[str, int]) -> str:
    return ", ".join(f"{v} {k}" for k, v in counts.items() if v and k != "unchanged") or "no changes"
