"""Benchmark search latency and break each query down by phase.

Searches a copy of your index (taken with SQLite's backup API, so your real index is
never touched) with a fixed set of queries, repeated, and reports p50/p95/p99 per phase:
embedding the query, BM25 (FTS5), KNN (sqlite-vec), fetching the chunk rows, and fusing
and filtering. If there is no index yet, one is built in the throwaway copy first.

    uv run python scripts/bench_search.py --vault "<path>"
    uv run python scripts/bench_search.py --vault "<path>" --queries my_queries.txt --repeat 50
    sudo uv run py-spy record -o search.svg -- python scripts/bench_search.py --vault "<path>"

Like bench_index.py, phases are timed by wrapping the searcher's collaborators, so the
engine code stays free of instrumentation.
"""
from __future__ import annotations

import argparse
import functools
import json
import logging
import math
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from vaultsearch.core import Config
from vaultsearch.core.embedder import make_embedder
from vaultsearch.core.indexer import Indexer
from vaultsearch.core.search import Searcher
from vaultsearch.core.store import Store

# (category, query). The categories are the shapes of query that stress different phases:
# single keywords hit many FTS rows, long questions are expensive to embed, Japanese has
# no spaces for the tokenizer, and two-letter prefixes are what you send while typing.
DEFAULT_QUERIES: list[tuple[str, str]] = [
    ("keyword", "grafos"),
    ("keyword", "kanji"),
    ("keyword", "python"),
    ("keyword", "docker"),
    ("natural", "how was I studying"),
    ("natural", "cómo funciona un árbol binario de búsqueda"),
    ("natural", "notes about job interviews"),
    ("long", "what did I write about the complexity of graph algorithms and how to prove they are correct"),
    ("long", "resumen de los temas de la asignatura de algoritmos con ejemplos de inducción y grafos"),
    ("japanese", "日本語の勉強"),
    ("japanese", "漢字"),
    ("prefix", "gr"),
    ("prefix", "alg"),
    ("prefix", "est"),
]

PHASES = ("embed query", "bm25", "knn", "fetch rows", "fuse + filter")


class PhaseTimer:
    """Wall time of each wrapped call, kept per call (not summed) so we can take percentiles."""

    def __init__(self) -> None:
        self.current: dict[str, float] = defaultdict(float)

    def wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def timed(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                self.current[name] += time.perf_counter() - t0

        return timed

    def take(self) -> dict[str, float]:
        out, self.current = dict(self.current), defaultdict(float)
        return out


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile: the smallest value with at least p% of the values at or below it."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(math.ceil(p / 100 * len(s)) - 1, 0)]


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "mean_ms": 1000 * sum(values) / len(values) if values else 0.0,
        "p50_ms": 1000 * percentile(values, 50),
        "p95_ms": 1000 * percentile(values, 95),
        "p99_ms": 1000 * percentile(values, 99),
        "max_ms": 1000 * max(values, default=0.0),
    }


def _copy_index(src: Path, dst: Path) -> None:
    """Snapshot the live index; unlike copying the file, this includes what is still in the WAL."""
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _instrument(searcher: Searcher, timer: PhaseTimer) -> None:
    searcher.embedder.embed = timer.wrap("embed query", searcher.embedder.embed)  # type: ignore[method-assign]
    searcher.store.fts_search = timer.wrap("bm25", searcher.store.fts_search)  # type: ignore[method-assign]
    searcher.store.vec_search = timer.wrap("knn", searcher.store.vec_search)  # type: ignore[method-assign]
    searcher.store.chunks_by_ids = timer.wrap("fetch rows", searcher.store.chunks_by_ids)  # type: ignore[method-assign]


def benchmark(
    cfg: Config,
    queries: list[tuple[str, str]] | None = None,
    repeat: int = 20,
    limit: int = 30,
    source_db: Path | None = None,
) -> dict[str, Any]:
    """Load the model, open (or build) the index in cfg.db_path, and time every query `repeat` times.

    `source_db`, if it exists, is copied into cfg.db_path first. `limit` defaults to 30,
    the number of results the TUI asks for.
    """
    queries = queries or DEFAULT_QUERIES
    built = False
    if source_db is not None and source_db.exists():
        _copy_index(source_db, cfg.db_path)

    t0 = time.perf_counter()
    embedder = make_embedder(cfg.embedder, cfg.embed_model, cfg.embed_batch_size, cfg.embed_threads)
    load_s = time.perf_counter() - t0

    model_id = cfg.embed_model if cfg.embedder == "fastembed" else cfg.embedder
    store = Store(cfg.db_path, embedder.dim, model_id)
    try:
        if store.stats()["chunks"] == 0:
            # No usable index (none yet, or the schema/model changed and Store wiped it)
            Indexer(cfg, store, embedder).sync_all()
            built = True
        stats = store.stats()

        searcher = Searcher(store, embedder)
        timer = PhaseTimer()
        _instrument(searcher, timer)

        # The very first query pays for ONNX/SQLite warm-up: report it apart, it's what
        # you feel on the first keystroke after launch.
        t0 = time.perf_counter()
        searcher.search(queries[0][1], limit=limit)
        first_s = time.perf_counter() - t0
        timer.take()

        totals: list[float] = []
        phases: dict[str, list[float]] = {p: [] for p in PHASES}
        by_category: dict[str, list[float]] = defaultdict(list)
        hits_per_query: dict[str, int] = {}
        for _ in range(repeat):
            for category, q in queries:
                t0 = time.perf_counter()
                hits = searcher.search(q, limit=limit)
                total = time.perf_counter() - t0
                spent = timer.take()
                spent["fuse + filter"] = max(total - sum(spent.values()), 0.0)
                totals.append(total)
                by_category[category].append(total)
                for p in PHASES:
                    phases[p].append(spent.get(p, 0.0))
                hits_per_query[q] = len(hits)
    finally:
        store.db.close()

    total_mean = sum(totals) / len(totals) if totals else 0.0
    return {
        "vault": str(cfg.vault),
        "embedder": model_id,
        "embed_threads": cfg.embed_threads,
        "index_built": built,
        "notes": stats["notes"],
        "chunks": stats["chunks"],
        "model_load_s": load_s,
        "limit": limit,
        "repeat": repeat,
        "queries": len(queries),
        "first_query_ms": 1000 * first_s,
        "total": summarize(totals),
        "qps": 1 / total_mean if total_mean else 0.0,
        "phases": [
            {"phase": p, **summarize(v), "share": (sum(v) / sum(totals)) if sum(totals) else 0.0}
            for p, v in phases.items()
        ],
        "categories": {c: summarize(v) for c, v in by_category.items()},
        "hits": hits_per_query,
    }


def _print_report(res: dict[str, Any]) -> None:
    from rich.console import Console
    from rich.table import Table

    con = Console(stderr=True)  # stdout stays free for --json
    con.print(f"[bold]Vault[/] {res['vault']}")
    note = " (built for this run)" if res["index_built"] else ""
    con.print(
        f"[bold]Index[/] {res['notes']} notes, {res['chunks']} chunks{note}; "
        f"embedder {res['embedder']} (load {res['model_load_s']:.2f} s, "
        f"{res['embed_threads'] or 'default'} threads)"
    )
    tot = res["total"]
    con.print(
        f"[bold]Search[/] {res['queries']} queries x {res['repeat']}, limit {res['limit']}: "
        f"p50 {tot['p50_ms']:.1f} ms, p95 {tot['p95_ms']:.1f} ms, p99 {tot['p99_ms']:.1f} ms, "
        f"{res['qps']:.0f} queries/s"
    )
    con.print(f"[bold]First query after load[/] {res['first_query_ms']:.1f} ms")

    def table(title: str, first: str, rows: list[tuple[str, dict[str, float], str]]) -> Table:
        t = Table(title=title, title_justify="left")
        t.add_column(first)
        for col in ("mean", "p50", "p95", "p99", "max"):
            t.add_column(f"{col} ms", justify="right")
        t.add_column("share", justify="right")
        for name, s, share in rows:
            t.add_row(name, *(f"{s[f'{c}_ms']:.2f}" for c in ("mean", "p50", "p95", "p99", "max")), share)
        return t

    con.print(table("Per phase", "phase", [(r["phase"], r, f"{r['share']:.0%}") for r in res["phases"]]))
    con.print(table("Per query shape (total)", "shape", [(c, s, "") for c, s in res["categories"].items()]))


def _read_queries(path: Path) -> list[tuple[str, str]]:
    """One query per line; `shape<TAB>query` groups them, blank lines and # comments are skipped."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        category, _, q = line.partition("\t") if "\t" in line else ("custom", "", line)
        out.append((category.strip(), q.strip()))
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", help="Vault path (or the VAULT_PATH env var)")
    ap.add_argument("--db", help="Index to copy (default: the vault's index in ~/.vaultsearch)")
    ap.add_argument("--embedder", choices=["fastembed", "hash"], help="Override VAULTSEARCH_EMBEDDER")
    ap.add_argument("--threads", type=int, help="ONNX intra-op threads (VAULTSEARCH_EMBED_THREADS)")
    ap.add_argument("--queries", type=Path, help="File with one query per line (optionally shape<TAB>query)")
    ap.add_argument("--repeat", type=int, default=20, help="Times each query is run (default 20)")
    ap.add_argument("--limit", type=int, default=30, help="Results per query (default 30, as in the TUI)")
    ap.add_argument("--json", action="store_true", help="Also print the raw results as JSON on stdout")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    src = Config.from_env(args.vault, db=args.db)
    with tempfile.TemporaryDirectory(prefix="vaultsearch-bench-") as tmp:
        cfg = replace(src, db_path=Path(tmp) / "bench.db")
        if args.embedder:
            cfg = replace(cfg, embedder=args.embedder)
        if args.threads:
            cfg = replace(cfg, embed_threads=args.threads)
        queries = _read_queries(args.queries) if args.queries else None
        res = benchmark(cfg, queries, repeat=args.repeat, limit=args.limit, source_db=src.db_path)

    _print_report(res)
    if args.json:
        print(json.dumps(res, indent=2))  # ASCII-escaped: a Windows console may be cp1252


if __name__ == "__main__":
    main()
