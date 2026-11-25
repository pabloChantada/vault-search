"""One-shot commands: index, search, status, watch. Output is rendered with Rich."""
from __future__ import annotations

import time
from datetime import datetime

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from ..core import Config, Engine
from ..core.text import query_words, snippet
from ..theme import ACCENT, DELETED, DIM, NEW, PATH

console = Console()  # results → stdout
err = Console(stderr=True)  # progress, spinners and logs → stderr


# ---------- helpers ----------
def _open_engine(cfg: Config, status_msg: str = "Loading embedding model…") -> Engine:
    engine = Engine(cfg)
    with err.status(f"[{DIM}]{status_msg}[/]", spinner="dots"):
        engine.open()
    return engine


def _sync_with_progress(engine: Engine, label: str = "Indexing") -> tuple[dict, float]:
    t0 = time.perf_counter()
    with Progress(
        SpinnerColumn(style=ACCENT),
        TextColumn(f"[bold]{label}[/]"),
        BarColumn(complete_style=ACCENT, finished_style=ACCENT),
        MofNCompleteColumn(),
        TextColumn("[{task.fields[style]}]{task.fields[note]}"),
        TimeElapsedColumn(),
        console=err,
        transient=True,
    ) as progress:
        task = progress.add_task(label, total=None, note="", style=DIM)

        def on_progress(done, total, rel, status):
            short = rel if len(rel) <= 48 else "…" + rel[-47:]
            style = ACCENT if status in ("new", "updated") else DIM
            progress.update(task, completed=done, total=total, note=short, style=style)

        counts = engine.indexer.sync_all(on_progress=on_progress)
    return counts, time.perf_counter() - t0


def _counts_line(counts: dict) -> Text:
    styles = {"new": NEW, "updated": ACCENT, "deleted": DELETED, "unchanged": DIM, "skipped": DIM}
    t = Text()
    for key, style in styles.items():
        if counts.get(key) or key == "unchanged":
            if t:
                t.append(" · ", style=DIM)
            t.append(str(counts.get(key, 0)), style=f"bold {style}")
            t.append(f" {key}", style=style)
    return t


def _print_hits(hits, query: str, elapsed_ms: float) -> None:
    if not hits:
        console.print(f"[{DIM}]No results for[/] “{query}”.\n")
        return
    words = query_words(query)
    plural = "s" if len(hits) != 1 else ""
    console.print(f"[{DIM}]{len(hits)} result{plural} · {elapsed_ms:.0f} ms[/]\n")
    for i, h in enumerate(hits, 1):
        head = Text()
        head.append(f"{i:>2}  ", style=f"bold {ACCENT}")
        head.append(h.path, style=f"bold {PATH}")
        if h.heading and h.heading != h.title:
            head.append(f"  › {h.heading}", style=DIM)
        console.print(head)
        body = Text(snippet(h.text, words))
        body.highlight_words(words, style=f"bold {ACCENT}", case_sensitive=False)
        console.print(Padding(body, (0, 0, 0, 4)), highlight=False)
        meta = f"score {h.score:.4f}"
        if h.tags:
            meta += "  ·  " + " ".join(f"#{t}" for t in h.tags[:6])
        console.print(Text("    " + meta, style=DIM))
        console.print()


def _stats_line(engine: Engine) -> str:
    s = engine.store.stats()
    return f"{s['notes']:,} notes · {s['chunks']:,} chunks"


# ---------- commands ----------
def cmd_index(cfg: Config) -> None:
    engine = _open_engine(cfg)
    counts, secs = _sync_with_progress(engine)
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    t.add_row("vault", str(cfg.vault))
    t.add_row("changes", _counts_line(counts))
    t.add_row("total", _stats_line(engine))
    t.add_row("index", f"{cfg.db_path}  ({cfg.db_path.stat().st_size / 1e6:.1f} MB)")
    t.add_row("time", f"{secs:.1f} s")
    console.print(Panel(t, title=f"[bold {ACCENT}]✓ Index up to date[/]", title_align="left",
                        border_style=ACCENT, expand=False, padding=(0, 2)))


def cmd_status(cfg: Config) -> None:
    engine = _open_engine(cfg, "Opening index…")
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM)
    t.add_column()
    t.add_row("vault", str(cfg.vault))
    t.add_row("index", str(cfg.db_path))
    t.add_row("model", cfg.embed_model)
    t.add_row("content", _stats_line(engine))
    console.print(Panel(t, title=f"[bold {ACCENT}]◆ vaultsearch[/]", title_align="left",
                        border_style=ACCENT, expand=False, padding=(0, 2)))
    recent = engine.store.list_files(None, 8)
    if recent:
        rt = Table(title="Recently modified", title_style="bold", title_justify="left",
                   box=None, show_header=False, padding=(0, 2))
        rt.add_column(style=PATH)
        rt.add_column(style=DIM, justify="right")
        for r in recent:
            rt.add_row(r["path"], datetime.fromtimestamp(r["mtime"]).strftime("%d %b %H:%M"))
        console.print(rt)


def cmd_search(cfg: Config, query: str | None, limit: int, folder: str | None, tag: str | None, sync: bool) -> None:
    engine = _open_engine(cfg)
    if sync:
        counts, _ = _sync_with_progress(engine, "Updating index")
        if any(counts[k] for k in ("new", "updated", "deleted")):
            err.print(Text("  ") + _counts_line(counts))

    def run(q: str) -> None:
        t0 = time.perf_counter()
        hits = engine.searcher.search(q, limit=limit, folder=folder, tag=tag)
        _print_hits(hits, q, (time.perf_counter() - t0) * 1000)

    if query:
        run(query)
        return

    # Simple prompt loop: the model is loaded once. (For the full experience, run `vaultsearch` with no args.)
    header = Text()
    header.append("◆ vaultsearch", style=f"bold {ACCENT}")
    header.append(f"  {cfg.vault.name}", style="bold")
    header.append(f"  ·  {_stats_line(engine)}", style=DIM)
    err.print(header)
    err.print(f"[{DIM}]Type a query · empty Enter or Ctrl+C to quit[/]\n")
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


def cmd_watch(cfg: Config) -> None:
    engine = Engine(cfg)
    engine.start(watch=True, background=True)
    err.print(f"[bold {ACCENT}]◆ watching[/] {cfg.vault}  [{DIM}]Ctrl+C to quit[/]")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        engine.stop()


def cmd_map(cfg: Config, out: str | None, method: str, sync: bool, open_browser: bool) -> None:
    from pathlib import Path

    from ..viz import build_graph, layout_cache, map_page, open_window, window_profile, write_html

    engine = _open_engine(cfg)
    if sync:
        _sync_with_progress(engine)
    with err.status(f"[{DIM}]Laying out the notes…[/]", spinner="dots"):
        graph = build_graph(engine.store, method, cache=layout_cache(cfg.db_path))
    if out:
        target = Path(out).expanduser().resolve()
    else:  # a map only meant to be looked at goes next to the index, not into the working directory
        target = map_page(cfg.db_path) if open_browser else Path("vault-map.html").resolve()
    path = write_html(graph, target, cfg.vault.name)
    err.print(
        f"[bold {ACCENT}]Map[/] {len(graph['nodes'])} notes · {len(graph['links'])} links "
        f"({graph['method'].upper()}) → [{PATH}]{path}[/]"
    )
    if open_browser:
        open_window(path, window_profile(cfg.db_path))
