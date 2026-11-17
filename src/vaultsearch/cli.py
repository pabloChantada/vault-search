"""CLI: vaultsearch {index,search,status,watch,serve}."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.logging import RichHandler
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from .search import WORD_RE

ACCENT = "#7DD3C0"
PATH = "#F2B66D"
DIM = "grey62"

console = Console()  # resultados → stdout
err = Console(stderr=True)  # progreso, spinners y logs → stderr


# ---------- helpers ----------
def _load(cfg, status_msg: str = "Loading embedding model…"):
    from .app import App

    app = App(cfg)
    with err.status(f"[{DIM}]{status_msg}[/]", spinner="dots"):
        app.open()
    return app


def _sync_with_progress(app, label: str = "Indexing") -> tuple[dict, float]:
    t0 = time.perf_counter()
    with Progress(
        SpinnerColumn(style=ACCENT),
        TextColumn(f"[bold]{label}[/]"),
        BarColumn(complete_style=ACCENT, finished_style=ACCENT),
        MofNCompleteColumn(),
        TextColumn("[{task.fields[style]}]{task.fields[note]}", justify="left"),
        TimeElapsedColumn(),
        console=err,
        transient=True,
    ) as progress:
        task = progress.add_task(label, total=None, note="", style=DIM)

        def on_progress(done, total, rel, status):
            short = rel if len(rel) <= 48 else "…" + rel[-47:]
            style = ACCENT if status in ("new", "updated") else DIM
            progress.update(task, completed=done, total=total, note=short, style=style)

        counts = app.indexer.sync_all(on_progress=on_progress)
    return counts, time.perf_counter() - t0


def _counts_line(counts: dict) -> Text:
    labels = [("new", "new", "#A5B4FC"), ("updated", "updated", ACCENT),
              ("deleted", "deleted", "#F9A8D4"), ("unchanged", "unchanged", DIM), ("skipped", "skipped", DIM)]
    t = Text()
    for key, name, style in labels:
        if counts.get(key) or key == "unchanged":
            if t:
                t.append(" · ", style=DIM)
            t.append(str(counts.get(key, 0)), style=f"bold {style}")
            t.append(f" {name}", style=style)
    return t


def _snippet(text: str, words: list[str], width: int = 320) -> str:
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    low = flat.lower()
    pos = min((low.find(w.lower()) for w in words if low.find(w.lower()) >= 0), default=0)
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


def _print_hits(hits, query: str, elapsed_ms: float) -> None:
    if not hits:
        console.print(f"[{DIM}]No results for[/] “{query}”.\n")
        return
    words = [w for w in WORD_RE.findall(query) if len(w) > 2]
    console.print(f"[{DIM}]{len(hits)} result{'s' if len(hits) != 1 else ''} · {elapsed_ms:.0f} ms[/]\n")
    for i, h in enumerate(hits, 1):
        head = Text()
        head.append(f"{i:>2}  ", style=f"bold {ACCENT}")
        head.append(h.path, style=f"bold {PATH}")
        if h.heading and h.heading != h.title:
            head.append(f"  › {h.heading}", style=DIM)
        console.print(head)
        body = Text(_snippet(h.text, words))
        body.highlight_words(words, style=f"bold {ACCENT}", case_sensitive=False)
        console.print(Padding(body, (0, 0, 0, 4)), highlight=False)
        meta = f"score {h.score:.4f}"
        if h.tags:
            meta += "  ·  " + " ".join(f"#{t}" for t in h.tags[:6])
        console.print(Text("    " + meta, style=DIM))
        console.print()


def _header(app) -> None:
    s = app.store.stats()
    t = Text()
    t.append("◆ vaultsearch", style=f"bold {ACCENT}")
    t.append(f"  {app.cfg.vault.name}", style="bold")
    t.append(f"  ·  {s['notas']} notes · {s['trozos']} chunks", style=DIM)
    err.print(t)


# ---------- comandos ----------
def cmd_index(cfg) -> None:
    app = _load(cfg)
    counts, secs = _sync_with_progress(app)
    s = app.store.stats()
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    t.add_row("vault", str(cfg.vault))
    t.add_row("changes", _counts_line(counts))
    t.add_row("total", f"{s['notas']} notes · {s['trozos']} chunks")
    t.add_row("index", f"{cfg.db_path}  ({cfg.db_path.stat().st_size / 1e6:.1f} MB)")
    t.add_row("time", f"{secs:.1f} s")
    console.print(Panel(t, title=f"[bold {ACCENT}]✓ Index up to date[/]", title_align="left",
                        border_style=ACCENT, expand=False, padding=(0, 2)))


def cmd_status(cfg) -> None:
    app = _load(cfg, "Opening index…")
    s = app.store.stats()
    recent = app.store.list_files(None, 8)
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    t.add_row("vault", str(cfg.vault))
    t.add_row("index", str(cfg.db_path))
    t.add_row("model", cfg.embed_model)
    t.add_row("content", f"{s['notas']} notes · {s['trozos']} chunks")
    console.print(Panel(t, title=f"[bold {ACCENT}]◆ vaultsearch[/]", title_align="left",
                        border_style=ACCENT, expand=False, padding=(0, 2)))
    if recent:
        rt = Table(title="Recently modified", title_style=f"bold", title_justify="left",
                   box=None, show_header=False, padding=(0, 2))
        rt.add_column(style=PATH)
        rt.add_column(style=DIM, justify="right")
        for r in recent:
            rt.add_row(r["path"], datetime.fromtimestamp(r["mtime"]).strftime("%d/%m %H:%M"))
        console.print(rt)


def cmd_search(cfg, query: str | None, limit: int, folder: str | None, tag: str | None, sync: bool) -> None:
    app = _load(cfg)
    if sync:
        counts, _ = _sync_with_progress(app, "Updating index")
        if any(counts[k] for k in ("new", "updated", "deleted")):
            err.print(Text("  ") + _counts_line(counts))

    def run(q: str) -> None:
        t0 = time.perf_counter()
        hits = app.searcher.search(q, limit=limit, folder=folder, tag=tag)
        _print_hits(hits, q, (time.perf_counter() - t0) * 1000)

    if query:
        run(query)
        return

    # Modo interactivo: el modelo se carga una vez
    _header(app)
    scope = "".join([f"  folder={folder}" if folder else "", f"  tag=#{tag}" if tag else ""])
    err.print(f"[{DIM}]Type a query · empty Enter or Ctrl+C to quit{scope}[/]\n")
    while True:
        try:
            q = console.input(f"[bold {ACCENT}]search ›[/] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            break
        if not q:
            break
        console.print()
        run(q)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="vaultsearch", description="Hybrid search over your Obsidian vault")
    ap.add_argument("--vault", help="Vault path (or VAULT_PATH env var)")
    ap.add_argument("--db", help="Index path (default ~/.vaultsearch/<vault>-<hash>.db)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("ui", help="Interactive search app (default when no command is given)")

    sub.add_parser("index", help="Index/update the vault with a progress bar")
    s = sub.add_parser("search", help="Search; with no query opens interactive mode")
    s.add_argument("query", nargs="?", help="Query (omit for interactive mode)")
    s.add_argument("-n", "--limit", type=int, default=8)
    s.add_argument("--folder", help="Restrict to a folder")
    s.add_argument("--tag", help="Restrict to a tag")
    s.add_argument("--no-sync", action="store_true", help="Don't update the index before searching")
    sub.add_parser("status", help="Index summary and recently modified notes")
    sub.add_parser("watch", help="Index and keep watching for changes (no MCP)")
    sub.add_parser("serve", help="MCP server over stdio (launched by Claude Desktop)")

    args = ap.parse_args(argv)
    args.cmd = args.cmd or "ui"

    if args.cmd == "serve":
        # stdout es el canal MCP: logs planos a stderr
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                            format="%(asctime)s %(levelname)s %(message)s")
    else:
        # En la CLI los logs por nota ensucian la barra: solo avisos, salvo -v
        logging.basicConfig(level=logging.INFO if args.verbose or args.cmd == "watch" else logging.WARNING,
                            format="%(message)s", datefmt="%H:%M:%S",
                            handlers=[RichHandler(console=err, show_path=False, markup=False)])
        logging.getLogger("httpx").setLevel(logging.WARNING)

    from .config import Config

    cfg = Config.from_env(args.vault, args.db)

    if args.cmd == "serve":
        from .server import serve

        serve(cfg)
    elif args.cmd == "ui":
        from .tui import run

        run(cfg)
    elif args.cmd == "index":
        cmd_index(cfg)
    elif args.cmd == "status":
        cmd_status(cfg)
    elif args.cmd == "search":
        cmd_search(cfg, args.query, args.limit, args.folder, args.tag, sync=not args.no_sync)
    elif args.cmd == "watch":
        from .app import App

        app = App(cfg)
        app.start(watch=True, background=True)
        err.print(f"[bold {ACCENT}]◆ watching[/] {cfg.vault}  [{DIM}]Ctrl+C to quit[/]")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
