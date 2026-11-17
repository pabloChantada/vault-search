"""Interactive terminal UI for vaultsearch (no LLM): live search, note preview, open in Obsidian."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.suggester import SuggestFromList
from textual.timer import Timer
from textual.widgets import Footer, Input, ListItem, ListView, Markdown, RichLog, Static

from .config import Config
from .search import WORD_RE, Hit

ACCENT = "#7DD3C0"
PATH = "#F2B66D"
DIM = "#9AA0A6"
NEW = "#A5B4FC"
DEL = "#F9A8D4"

COMMANDS = {
    "/recent": "recently modified notes",
    "/folder": "scope search to a folder (/folder off to clear)",
    "/tag": "scope search to a tag (/tag off to clear)",
    "/reindex": "force a full rescan of the vault",
    "/status": "index stats",
    "/clear": "clear results",
    "/help": "shortcuts and commands",
    "/quit": "exit",
}

HELP_MD = """\
# vaultsearch

Type to search — results update as you type.

| Key | Action |
|---|---|
| `↓` / `Enter` in the prompt | jump to results |
| `↑` `↓` | move through results (preview updates) |
| `Enter` / `o` | open note in **Obsidian** |
| `e` | open note in the default editor |
| `y` | copy the note path |
| `/` or `Esc` | back to the prompt |
| `Ctrl+Q` | quit |

Clicking a result's path also opens it in Obsidian.

## Commands

""" + "\n".join(f"- `{c}` — {d}" for c, d in COMMANDS.items())


class _UILogHandler(logging.Handler):
    """Routes library logs (watcher/indexer) into the TUI's event log."""

    def __init__(self, tui: "VaultSearchApp"):
        super().__init__(logging.INFO)
        self.tui = tui

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.tui.call_from_thread(self.tui.log_event, record.levelno, record.getMessage())
        except Exception:
            pass


class ResultItem(ListItem):
    def __init__(self, idx: int, path: str, heading: str, body: Text, meta: str):
        super().__init__()
        self.path = path
        self.heading = heading
        self._idx = idx
        self._body = body
        self._meta = meta

    def compose(self) -> ComposeResult:
        head = Text()
        head.append(f"{self._idx:>2}  ", style=f"bold {ACCENT}")
        head.append(self.path, style=f"bold {PATH}")
        if self.heading:
            head.append(f"  › {self.heading}", style=DIM)
        yield Static(head, classes="r-head")
        if self._body:
            yield Static(self._body, classes="r-body")
        if self._meta:
            yield Static(Text(self._meta, style=DIM), classes="r-meta")


class CommandItem(ListItem):
    def __init__(self, command: str, desc: str):
        super().__init__()
        self.command = command
        self._desc = desc

    def compose(self) -> ComposeResult:
        t = Text()
        t.append(f"{self.command:<10}", style=f"bold {ACCENT}")
        t.append(self._desc, style=DIM)
        yield Static(t)


class VaultSearchApp(App):
    TITLE = "vaultsearch"
    CSS = f"""
    Screen {{ background: #0F1115; color: #E6E6E3; }}
    #header {{ border: round {ACCENT}; padding: 0 2; height: auto; width: auto; max-width: 100%; margin: 0 1; }}
    #main {{ height: 1fr; margin: 0 1; }}
    #results {{ width: 45%; border: round #262A33; background: #0F1115; }}
    #results:focus-within {{ border: round {ACCENT}; }}
    #results ListItem {{ padding: 0 1; margin-bottom: 1; background: #0F1115; }}
    #results ListItem.-highlight {{ background: #1C2029; }}
    #results > ListItem.-highlight .r-head {{ text-style: none; }}
    .r-body {{ padding-left: 4; color: #E6E6E3; }}
    .r-meta {{ padding-left: 4; }}
    #preview-wrap {{ width: 55%; border: round #262A33; padding: 0 1; }}
    #preview-wrap:focus-within {{ border: round {ACCENT}; }}
    #preview {{ background: #0F1115; }}
    #events {{ height: 5; margin: 0 1; border: round #262A33; background: #0F1115; }}
    #prompt {{ margin: 0 1; border: round #3A3F4B; background: #0F1115; }}
    #prompt:focus {{ border: round {ACCENT}; }}
    #status {{ height: 1; margin: 0 2; color: {DIM}; }}
    Footer {{ background: #161920; }}
    """

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
        from .app import App as Core

        self.core = Core(cfg)
        self.ready = threading.Event()
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
        handler = _UILogHandler(self)
        root = logging.getLogger("vaultsearch")
        root.handlers[:] = [handler]
        root.setLevel(logging.INFO)
        root.propagate = False
        self.query_one("#prompt", Input).focus()
        self._refresh_status()
        self.set_interval(1.0, self._refresh_status)
        self.boot()

    # ---------- boot: model, watcher, initial sync (background thread) ----------
    @work(thread=True, exclusive=True, group="boot")
    def boot(self) -> None:
        core = self.core
        try:
            core.open()
        except Exception as e:  # e.g. model download failed
            self.state = f"error: {e}"
            self.call_from_thread(self.log_event, logging.ERROR, f"Startup failed: {e}")
            return
        from .watcher import VaultWatcher

        core.watcher = VaultWatcher(core.indexer, debounce_s=self.cfg.debounce_s)
        core.watcher.start()
        self.ready.set()
        self.state = "indexing"
        self.call_from_thread(self._update_header)
        t0 = time.perf_counter()

        def on_progress(done, total, rel, status):
            self.progress = f"{done}/{total}"

        counts = core.indexer.sync_all(on_progress=on_progress)
        self.progress = ""
        self.state = "ready"
        changed = ", ".join(f"{v} {k}" for k, v in counts.items() if v and k != "unchanged") or "no changes"
        self.call_from_thread(self.log_event, logging.INFO,
                              f"Initial sync: {changed} ({time.perf_counter() - t0:.1f}s)")
        self.call_from_thread(self._update_header)
        if self._last_query:
            self.call_from_thread(self.run_search, self._last_query)

    # ---------- header / status ----------
    def _header_text(self) -> Text:
        t = Text()
        t.append("◆ vaultsearch", style=f"bold {ACCENT}")
        t.append("  v0.1.0\n", style=DIM)
        t.append("vault: ", style=DIM)
        t.append(self.cfg.vault.name, style="bold")
        if self.core.store:
            s = self.core.store.stats()
            t.append(f" · {s['notas']:,} notes · {s['trozos']:,} chunks", style=DIM)
        t.append(" · F1 help · Ctrl+Q quit", style=DIM)
        return t

    def _update_header(self) -> None:
        self.query_one("#header", Static).update(self._header_text())

    def _refresh_status(self) -> None:
        t = Text()
        if self.state == "ready":
            t.append("◉ ", style=ACCENT)
            t.append("index up to date · watching for changes")
        elif self.state.startswith("error"):
            t.append("✕ ", style=DEL)
            t.append(self.state)
        else:
            t.append("◌ ", style=PATH)
            t.append(self.state + (f" {self.progress}" if self.progress else ""))
        scope = []
        if self.folder:
            scope.append(f"folder={self.folder}")
        if self.tag:
            scope.append(f"tag=#{self.tag}")
        if scope:
            t.append("  ·  scope: " + " ".join(scope), style=PATH)
        t.append(f"  ·  {self.cfg.embed_model.split('/')[-1]}", style=DIM)
        self.query_one("#status", Static).update(t)

    def log_event(self, level: int, msg: str) -> None:
        t = Text()
        t.append(datetime.now().strftime("%H:%M:%S  "), style="#6B7280")
        style = (DEL if level >= logging.WARNING or msg.startswith("Removed") else
                 PATH if msg.startswith("Renamed") else
                 NEW if msg.startswith(("Initial", "Rescan", "Scope")) else ACCENT)
        t.append(msg, style=style)
        self.query_one("#events", RichLog).write(t)
        if self.core.store:
            self._update_header()

    # ---------- input ----------
    @on(Input.Changed, "#prompt")
    def _on_change(self, event: Input.Changed) -> None:
        q = event.value.strip()
        if q.startswith("/"):
            self._show_command_hints(q)
            return
        if self._debounce:
            self._debounce.stop()
        if not q:
            return
        self._debounce = self.set_timer(0.25, lambda: self.run_search(q))

    @on(Input.Submitted, "#prompt")
    def _on_submit(self, event: Input.Submitted) -> None:
        q = event.value.strip()
        if q.startswith("/"):
            self.run_command(q)
            event.input.value = ""
            return
        if q:
            self.run_search(q)
        self._focus_results()

    def on_key(self, event) -> None:
        if event.key == "down" and self.focused is self.query_one("#prompt", Input):
            self._focus_results()
            event.stop()

    def _focus_results(self) -> None:
        lv = self.query_one("#results", ListView)
        if lv.children:
            lv.focus()
            if lv.index is None:
                lv.index = 0

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt", Input).focus()

    # ---------- search ----------
    @work(thread=True, exclusive=True, group="search")
    def run_search(self, query: str) -> None:
        self._last_query = query
        if not self.ready.wait(0.05):
            self.call_from_thread(self._set_results_title, "results · waiting for model…")
            return
        t0 = time.perf_counter()
        hits = self.core.searcher.search(query, limit=30, folder=self.folder, tag=self.tag)
        ms = (time.perf_counter() - t0) * 1000
        self.call_from_thread(self._show_hits, query, hits, ms)

    def _set_results_title(self, title: str) -> None:
        self.query_one("#results", ListView).border_title = title

    def _show_hits(self, query: str, hits: list[Hit], ms: float) -> None:
        if query != self._last_query:
            return  # a newer query is on its way
        words = [w for w in WORD_RE.findall(query) if len(w) > 2]
        items = []
        for i, h in enumerate(hits, 1):
            body = Text(_snippet(h.text, words, 220))
            body.highlight_words(words, style=f"bold {ACCENT}", case_sensitive=False)
            heading = h.heading if h.heading != h.title else ""
            meta = " ".join(f"#{t}" for t in h.tags[:5])
            items.append(ResultItem(i, h.path, heading, body, meta))
        self._fill_results(items, f"results · {len(hits)} · {ms:.0f} ms")
        if not hits:
            self.query_one("#preview", Markdown).update(f"No results for **{query}**.")

    def _fill_results(self, items: list[ListItem], title: str) -> None:
        lv = self.query_one("#results", ListView)
        lv.clear()
        if items:
            lv.extend(items)
            lv.index = 0
            if isinstance(items[0], ResultItem):
                self.load_preview(items[0].path, items[0].heading)
        lv.border_title = title

    # ---------- preview ----------
    @on(ListView.Highlighted, "#results")
    def _on_highlight(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, ResultItem):
            self.load_preview(item.path, item.heading)

    @work(exclusive=True, group="preview")
    async def load_preview(self, rel: str, heading: str) -> None:
        p = self.cfg.vault / rel
        try:
            text = _preview_markdown(p.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            text = f"_Could not read {rel}: {e}_"
        wrap = self.query_one("#preview-wrap", VerticalScroll)
        wrap.border_title = rel
        md = self.query_one("#preview", Markdown)
        await md.update(text)
        wrap.scroll_home(animate=False)
        # Jump to the matching section if we can find its heading
        last = heading.split(" > ")[-1].strip() if heading else ""
        if last:
            for block in md.query("MarkdownH1, MarkdownH2, MarkdownH3, MarkdownH4, MarkdownH5, MarkdownH6"):
                src = getattr(block, "source", None) or ""
                if last in src:
                    wrap.scroll_to_widget(block, top=True, animate=False)
                    break

    # ---------- opening notes ----------
    def _selected_path(self) -> str | None:
        lv = self.query_one("#results", ListView)
        item = lv.highlighted_child
        return item.path if isinstance(item, ResultItem) else None

    @on(ListView.Selected, "#results")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ResultItem):
            self.action_open_obsidian(event.item.path)
        elif isinstance(event.item, CommandItem):
            self.query_one("#prompt", Input).value = ""
            self.run_command(event.item.command)

    def action_open_selected(self, how: str) -> None:
        if isinstance(self.focused, Input):
            return  # typing 'o'/'e' in the prompt must not open anything
        rel = self._selected_path()
        if not rel:
            return
        if how == "obsidian":
            self.action_open_obsidian(rel)
        else:
            self.action_open_editor(rel)

    def action_open_obsidian(self, rel: str) -> None:
        file = rel[:-3] if rel.lower().endswith(".md") else rel
        uri = f"obsidian://open?vault={quote(self.cfg.vault.name)}&file={quote(file)}"
        webbrowser.open(uri)
        self.notify(f"Opening in Obsidian: {rel}", timeout=2)

    def action_open_editor(self, rel: str) -> None:
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

    # ---------- commands ----------
    def _show_command_hints(self, q: str) -> None:
        head = q.split()[0].lower()
        items = [CommandItem(c, d) for c, d in COMMANDS.items() if c.startswith(head)]
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
            self._refresh_status()
            self.log_event(logging.INFO, f"Scope: folder={self.folder or 'all'}")
        elif cmd == "/tag":
            self.tag = None if arg in ("", "off") else arg.lstrip("#")
            self._refresh_status()
            self.log_event(logging.INFO, f"Scope: tag={'#' + self.tag if self.tag else 'all'}")
        elif cmd == "/recent":
            self.show_recent(int(arg) if arg.isdigit() else 30)
        elif cmd == "/status":
            if self.core.store:
                s = self.core.store.stats()
                self.log_event(logging.INFO, f"{s['notas']:,} notes · {s['trozos']:,} chunks · index {self.cfg.db_path}")
        elif cmd == "/reindex":
            self.reindex()
        else:
            self.notify(f"Unknown command {cmd}. Try /help", severity="warning")
        if self._last_query and cmd in ("/folder", "/tag"):
            self.run_search(self._last_query)

    @work(thread=True, group="recent")
    def show_recent(self, n: int) -> None:
        if not self.ready.wait(30):
            return
        rows = self.core.store.list_files(self.folder, n)

        def build():
            import json

            items = []
            for i, r in enumerate(rows, 1):
                when = datetime.fromtimestamp(r["mtime"]).strftime("%d %b %H:%M")
                tags = " ".join(f"#{t}" for t in json.loads(r["tags"])[:5])
                items.append(ResultItem(i, r["path"], "", Text(when, style=DIM), tags))
            self._fill_results(items, f"recent · {len(rows)}")
            self._focus_results()

        self.call_from_thread(build)

    @work(thread=True, exclusive=True, group="reindex")
    def reindex(self) -> None:
        if not self.ready.wait(30):
            return
        self.state = "reindexing"

        def on_progress(done, total, rel, status):
            self.progress = f"{done}/{total}"

        counts = self.core.indexer.sync_all(on_progress=on_progress)
        self.progress = ""
        self.state = "ready"
        changed = ", ".join(f"{v} {k}" for k, v in counts.items() if v and k != "unchanged") or "no changes"
        self.call_from_thread(self.log_event, logging.INFO, f"Rescan: {changed}")

    def on_unmount(self) -> None:
        if self.core.watcher:
            self.core.watcher.stop()


def _preview_markdown(raw: str) -> str:
    """Frontmatter → a tags line; [[wikilinks]] → bold text (Markdown widget doesn't know them)."""
    import re

    import frontmatter

    try:
        post = frontmatter.loads(raw)
        body, meta = post.content, post.metadata
    except Exception:
        body, meta = raw, {}
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    body = re.sub(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]", lambda m: f"**{m.group(2) or m.group(1)}**", body)
    head = ("`" + "`  `".join(f"#{str(t).lstrip('#')}" for t in tags) + "`\n\n") if tags else ""
    return head + body


def _snippet(text: str, words: list[str], width: int) -> str:
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    low = flat.lower()
    found = [low.find(w.lower()) for w in words if low.find(w.lower()) >= 0]
    pos = min(found) if found else 0
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


def run(cfg: Config) -> None:
    VaultSearchApp(cfg).run()
