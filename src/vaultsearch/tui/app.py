"""The interactive app: live search, note preview, open in Obsidian, live watcher log."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from urllib.parse import quote

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Key
from textual.suggester import SuggestFromList
from textual.timer import Timer
from textual.widgets import Footer, Input, ListItem, ListView, Markdown, RichLog, Static

from .. import __version__
from ..core import Config, Engine, Hit
from ..core.text import preview_markdown, query_words, snippet
from ..theme import ACCENT, DELETED, DIM, NEW, PATH
from .help import COMMANDS, HELP_MD
from .widgets import CommandItem, ResultItem


class _PanelLogHandler(logging.Handler):
    """Routes engine logs (indexer/watcher, other threads) into the TUI's watcher panel."""

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

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("escape", "focus_prompt", "Prompt", show=False),
        Binding("slash", "focus_prompt", "Prompt", show=False),
        Binding("o", "open_selected('obsidian')", "Open in Obsidian"),
        Binding("e", "open_selected('editor')", "Open in editor"),
        Binding("y", "copy_path", "Copy path"),
        Binding("f1", "help", "Help"),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.engine = Engine(cfg)
        self.state = "loading model…"
        self.progress = ""
        self.folder: str | None = None
        self.tag: str | None = None
        self._debounce: Timer | None = None
        self._last_query = ""

    # ---------- layout ----------
    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), id="header")
        with Horizontal(id="main"):
            yield ListView(id="results")
            with VerticalScroll(id="preview-wrap"):
                yield Markdown(HELP_MD, id="preview")
        yield RichLog(id="events", max_lines=500, wrap=False, markup=False)
        yield Input(
            placeholder="Search your vault…  (/ for commands)",
            id="prompt",
            suggester=SuggestFromList(list(COMMANDS), case_sensitive=False),
        )
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#events", RichLog).border_title = "watcher"
        self.query_one("#results", ListView).border_title = "results"
        self.query_one("#preview-wrap").border_title = "preview"
        engine_log = logging.getLogger("vaultsearch")
        engine_log.handlers[:] = [_PanelLogHandler(self)]
        engine_log.setLevel(logging.INFO)
        engine_log.propagate = False
        self.query_one("#prompt", Input).focus()
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
        self.call_from_thread(self._update_header)
        t0 = time.perf_counter()
        counts = self.engine.indexer.sync_all(on_progress=self._on_progress)
        self.progress = ""
        self.state = "ready"
        self.call_from_thread(self.log_event, logging.INFO,
                              f"Initial sync: {_describe(counts)} ({time.perf_counter() - t0:.1f}s)")
        self.call_from_thread(self._update_header)
        if self._last_query:
            self.call_from_thread(self.run_search, self._last_query)

    def _on_progress(self, done: int, total: int, rel: str, status: str) -> None:
        self.progress = f"{done}/{total}"

    # ---------- header / status bar / watcher panel ----------
    def _header_text(self) -> Text:
        t = Text()
        t.append("◆ vaultsearch", style=f"bold {ACCENT}")
        t.append(f"  v{__version__}\n", style=DIM)
        t.append("vault ", style=DIM)
        t.append(self.cfg.vault.name, style="bold")
        if self.engine.store:
            s = self.engine.store.stats()
            t.append(f"  ·  {s['notes']:,} notes  ·  {s['chunks']:,} chunks", style=DIM)
        t.append("  ·  F1 help  ·  Ctrl+Q quit", style=DIM)
        return t

    def _update_header(self) -> None:
        self.query_one("#header", Static).update(self._header_text())

    def _refresh_status(self) -> None:
        t = Text()
        if self.state == "ready":
            t.append("◉ ", style=ACCENT)
            t.append("index up to date · watching for changes")
        elif self.state.startswith("error"):
            t.append("✕ ", style=DELETED)
            t.append(self.state)
        else:
            t.append("◌ ", style=PATH)
            t.append(self.state + (f" {self.progress}" if self.progress else ""))
        scope = [f"folder={self.folder}"] if self.folder else []
        scope += [f"tag=#{self.tag}"] if self.tag else []
        if scope:
            t.append("  ·  scope: " + " ".join(scope), style=PATH)
        t.append(f"  ·  {self.cfg.embed_model.split('/')[-1]}", style=DIM)
        self.query_one("#status", Static).update(t)

    def log_event(self, level: int, msg: str) -> None:
        style = (DELETED if level >= logging.WARNING or msg.startswith("Removed") else
                 PATH if msg.startswith("Renamed") else
                 NEW if msg.startswith(("Initial", "Rescan", "Scope")) else ACCENT)
        t = Text()
        t.append(datetime.now().strftime("%H:%M:%S  "), style="#6B7280")
        t.append(msg, style=style)
        self.query_one("#events", RichLog).write(t)
        if self.engine.store:
            self._update_header()

    # ---------- prompt ----------
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
            self.run_search(q)
        self._focus_results()

    def on_key(self, event: Key) -> None:
        if event.key == "down" and self.focused is self.query_one("#prompt", Input):
            self._focus_results()
            event.stop()

    def _focus_results(self) -> None:
        results = self.query_one("#results", ListView)
        if results.children:
            results.focus()
            if results.index is None:
                results.index = 0

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt", Input).focus()

    # ---------- search ----------
    @work(thread=True, exclusive=True, group="search")
    def run_search(self, query: str) -> None:
        self._last_query = query
        if not self.engine.ready.wait(0.05):
            self.call_from_thread(self._set_results_title, "results · waiting for the model…")
            return  # boot() re-runs the last query once it's ready
        t0 = time.perf_counter()
        hits = self.engine.searcher.search(query, limit=30, folder=self.folder, tag=self.tag)
        self.call_from_thread(self._show_hits, query, hits, (time.perf_counter() - t0) * 1000)

    def _set_results_title(self, title: str) -> None:
        self.query_one("#results", ListView).border_title = title

    def _show_hits(self, query: str, hits: list[Hit], ms: float) -> None:
        if query != self._last_query:
            return  # a newer query is on its way
        words = query_words(query)
        items: list[ListItem] = []
        for i, h in enumerate(hits, 1):
            body = Text(snippet(h.text, words, 220))
            body.highlight_words(words, style=f"bold {ACCENT}", case_sensitive=False)
            heading = h.heading if h.heading != h.title else ""
            items.append(ResultItem(i, h.path, heading, body, " ".join(f"#{t}" for t in h.tags[:5])))
        self._fill_results(items, f"results · {len(hits)} · {ms:.0f} ms")
        if not hits:
            self.query_one("#preview", Markdown).update(f"No results for **{query}**.")

    def _fill_results(self, items: list[ListItem], title: str) -> None:
        results = self.query_one("#results", ListView)
        results.clear()
        if items:
            results.extend(items)
            results.index = 0
            if isinstance(items[0], ResultItem):
                self.load_preview(items[0].path, items[0].heading)
        results.border_title = title

    # ---------- preview ----------
    @on(ListView.Highlighted, "#results")
    def _on_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, ResultItem):
            self.load_preview(event.item.path, event.item.heading)

    @work(exclusive=True, group="preview")
    async def load_preview(self, rel: str, heading: str) -> None:
        try:
            text = preview_markdown((self.cfg.vault / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            text = f"_Could not read {rel}: {e}_"
        wrap = self.query_one("#preview-wrap", VerticalScroll)
        wrap.border_title = rel
        md = self.query_one("#preview", Markdown)
        await md.update(text)
        wrap.scroll_home(animate=False)
        # Jump to the section the hit came from
        section = heading.split(" > ")[-1].strip() if heading else ""
        if section:
            for block in md.query("MarkdownH1, MarkdownH2, MarkdownH3, MarkdownH4, MarkdownH5, MarkdownH6"):
                if section in (getattr(block, "source", None) or ""):
                    wrap.scroll_to_widget(block, top=True, animate=False)
                    break

    # ---------- opening notes ----------
    def _selected_path(self) -> str | None:
        item = self.query_one("#results", ListView).highlighted_child
        return item.path if isinstance(item, ResultItem) else None

    @on(ListView.Selected, "#results")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ResultItem):
            self.open_in_obsidian(event.item.path)
        elif isinstance(event.item, CommandItem):
            self.query_one("#prompt", Input).value = ""
            self.run_command(event.item.command)

    def action_open_selected(self, how: str) -> None:
        if isinstance(self.focused, Input):
            return  # typing 'o' or 'e' in the prompt must not open anything
        rel = self._selected_path()
        if rel:
            self.open_in_obsidian(rel) if how == "obsidian" else self.open_in_editor(rel)

    def open_in_obsidian(self, rel: str) -> None:
        file = rel[:-3] if rel.lower().endswith(".md") else rel
        webbrowser.open(f"obsidian://open?vault={quote(self.cfg.vault.name)}&file={quote(file)}")
        self.notify(f"Opening in Obsidian: {rel}", timeout=2)

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
        if isinstance(self.focused, Input):
            return
        rel = self._selected_path()
        if rel:
            self.copy_to_clipboard(str(self.cfg.vault / rel))
            self.notify("Path copied", timeout=2)

    def action_help(self) -> None:
        self.query_one("#preview-wrap").border_title = "help"
        self.query_one("#preview", Markdown).update(HELP_MD)

    # ---------- slash commands ----------
    def _show_command_hints(self, q: str) -> None:
        typed = q.split()[0].lower()
        items = [CommandItem(c, d) for c, d in COMMANDS.items() if c.startswith(typed)]
        self._fill_results(items, "commands · Enter to run · → to complete")

    def run_command(self, raw: str) -> None:
        parts = raw.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        if cmd == "/quit":
            self.exit()
        elif cmd == "/help":
            self.action_help()
        elif cmd == "/clear":
            self._last_query = ""
            self._fill_results([], "results")
        elif cmd == "/folder":
            self.folder = None if arg in ("", "off") else arg.strip("/")
            self.log_event(logging.INFO, f"Scope: folder={self.folder or 'all'}")
        elif cmd == "/tag":
            self.tag = None if arg in ("", "off") else arg.lstrip("#")
            self.log_event(logging.INFO, f"Scope: tag={'#' + self.tag if self.tag else 'all'}")
        elif cmd == "/recent":
            self.show_recent(int(arg) if arg.isdigit() else 30)
        elif cmd == "/status":
            if self.engine.store:
                s = self.engine.store.stats()
                self.log_event(logging.INFO, f"{s['notes']:,} notes · {s['chunks']:,} chunks · index at {self.cfg.db_path}")
        elif cmd == "/reindex":
            self.reindex()
        else:
            self.notify(f"Unknown command {cmd}. Try /help", severity="warning")
            return
        if cmd in ("/folder", "/tag"):
            self._refresh_status()
            if self._last_query:
                self.run_search(self._last_query)

    @work(thread=True, group="recent")
    def show_recent(self, n: int) -> None:
        if not self.engine.ready.wait(30):
            return
        rows = self.engine.store.list_files(self.folder, n)

        def build() -> None:
            items: list[ListItem] = []
            for i, r in enumerate(rows, 1):
                when = Text(datetime.fromtimestamp(r["mtime"]).strftime("%d %b %H:%M"), style=DIM)
                tags = " ".join(f"#{t}" for t in json.loads(r["tags"])[:5])
                items.append(ResultItem(i, r["path"], "", when, tags))
            self._fill_results(items, f"recent · {len(rows)}")
            self._focus_results()

        self.call_from_thread(build)

    @work(thread=True, exclusive=True, group="reindex")
    def reindex(self) -> None:
        if not self.engine.ready.wait(30):
            return
        self.state = "reindexing"
        counts = self.engine.indexer.sync_all(on_progress=self._on_progress)
        self.progress = ""
        self.state = "ready"
        self.call_from_thread(self.log_event, logging.INFO, f"Rescan: {_describe(counts)}")


def _describe(counts: dict[str, int]) -> str:
    return ", ".join(f"{v} {k}" for k, v in counts.items() if v and k != "unchanged") or "no changes"
