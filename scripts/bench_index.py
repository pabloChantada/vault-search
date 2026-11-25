"""Benchmark a full index build and break the time down by phase.

Builds the index from scratch into a throwaway database (your real index is never
touched), then runs a second, warm sync where nothing changed. Run it on its own or
under a profiler:

    uv run python scripts/bench_index.py --vault "<path>"
    uv run scalene run scripts/bench_index.py --- --vault "<path>"   (then: scalene view)
    sudo uv run py-spy record -o index.svg -- python scripts/bench_index.py --vault "<path>"

Phases are timed by wrapping the indexer's collaborators, so the engine code itself
stays free of instrumentation. Whatever is not covered by a wrapped call (reading the
file, SHA-1 of its bytes, stat, small lookups) is reported as "read + hash + other".
"""
from __future__ import annotations

import argparse
import functools
import json
import logging
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from vaultsearch.core import Config
from vaultsearch.core import indexer as indexer_mod
from vaultsearch.core.embedder import make_embedder
from vaultsearch.core.indexer import Indexer
from vaultsearch.core.store import Store


class PhaseTimer:
    """Accumulates wall time and call counts per named phase."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)

    def wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def timed(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                self.seconds[name] += time.perf_counter() - t0
                self.calls[name] += 1

        return timed


def _instrument(indexer: Indexer, timer: PhaseTimer, counters: dict[str, int]) -> None:
    """Patch the indexer's collaborators (instance-level, plus the module-level parse_note)."""
    walk = indexer.iter_notes
    indexer.iter_notes = timer.wrap("walk vault", lambda: list(walk()))  # type: ignore[method-assign]

    embed = indexer.embedder.embed

    def counting_embed(texts: list[str]) -> list[list[float]]:
        counters["texts embedded"] += len(texts)
        counters["chars embedded"] += sum(len(t) for t in texts)
        return embed(texts)

    indexer.embedder.embed = timer.wrap("embed", counting_embed)  # type: ignore[method-assign]
    indexer.store.write_notes = timer.wrap("sqlite write", indexer.store.write_notes)  # type: ignore[method-assign]
    indexer_mod.parse_note = timer.wrap("parse + chunk", indexer_mod.parse_note)


def _run_sync(indexer: Indexer) -> tuple[float, PhaseTimer, dict[str, int], dict[str, int]]:
    timer, counters = PhaseTimer(), defaultdict(int)
    original_parse = indexer_mod.parse_note
    _instrument(indexer, timer, counters)
    try:
        t0 = time.perf_counter()
        counts = indexer.sync_all()
        total = time.perf_counter() - t0
    finally:
        # Undo the patches so a second run measures from a clean slate
        indexer_mod.parse_note = original_parse
        for obj, attr in ((indexer, "iter_notes"), (indexer.embedder, "embed"), (indexer.store, "write_notes")):
            obj.__dict__.pop(attr, None)
    return total, timer, dict(counters), counts


def _phase_report(total: float, timer: PhaseTimer) -> list[dict[str, Any]]:
    rows = [{"phase": name, "seconds": s, "calls": timer.calls[name]} for name, s in timer.seconds.items()]
    other = max(total - sum(timer.seconds.values()), 0.0)
    rows.append({"phase": "read + hash + other", "seconds": other, "calls": None})
    for r in rows:
        r["share"] = r["seconds"] / total if total else 0.0
    return sorted(rows, key=lambda r: r["seconds"], reverse=True)


def benchmark(cfg: Config) -> dict[str, Any]:
    """Load the model, index the vault from scratch, then run a warm (no-op) sync."""
    t0 = time.perf_counter()
    embedder = make_embedder(cfg.embedder, cfg.embed_model, cfg.embed_batch_size, cfg.embed_threads)
    load_s = time.perf_counter() - t0

    model_id = cfg.embed_model if cfg.embedder == "fastembed" else cfg.embedder
    store = Store(cfg.db_path, embedder.dim, model_id)
    try:
        indexer = Indexer(cfg, store, embedder)
        cold_s, cold_timer, cold_counters, cold_counts = _run_sync(indexer)
        stats = store.stats()
        warm_s, warm_timer, _, warm_counts = _run_sync(indexer)
    finally:
        store.db.close()

    return {
        "vault": str(cfg.vault),
        "embedder": model_id,
        "embed_batch_size": cfg.embed_batch_size,
        "embed_threads": cfg.embed_threads,
        "index_batch_chunks": cfg.index_batch_chunks,
        "model_load_s": load_s,
        "notes": stats["notes"],
        "chunks": stats["chunks"],
        "cold": {
            "seconds": cold_s,
            "notes_per_s": stats["notes"] / cold_s if cold_s else 0.0,
            "chunks_per_s": stats["chunks"] / cold_s if cold_s else 0.0,
            "counts": cold_counts,
            "counters": cold_counters,
            "phases": _phase_report(cold_s, cold_timer),
        },
        "warm": {"seconds": warm_s, "counts": warm_counts, "phases": _phase_report(warm_s, warm_timer)},
    }


def _print_report(res: dict[str, Any]) -> None:
    from rich.console import Console
    from rich.table import Table

    con = Console(stderr=True)  # stdout stays free for --json
    cold, warm = res["cold"], res["warm"]
    con.print(f"[bold]Vault[/] {res['vault']}")
    con.print(
        f"[bold]Embedder[/] {res['embedder']} (load {res['model_load_s']:.2f} s, "
        f"ONNX batch {res['embed_batch_size']}, {res['embed_threads'] or 'default'} threads, "
        f"index batch {res['index_batch_chunks']} chunks)"
    )
    con.print(
        f"[bold]Cold build[/] {res['notes']} notes, {res['chunks']} chunks in {cold['seconds']:.2f} s "
        f"→ {cold['notes_per_s']:.1f} notes/s, {cold['chunks_per_s']:.1f} chunks/s"
    )
    c = cold["counters"]
    if c.get("texts embedded"):
        avg = c["chars embedded"] / c["texts embedded"]
        con.print(f"           {c['texts embedded']} texts embedded, avg {avg:.0f} chars")

    for title, run in (("Cold build", cold), (f"Warm sync ({warm['seconds'] * 1000:.0f} ms)", warm)):
        t = Table(title=title, title_justify="left")
        t.add_column("phase")
        t.add_column("seconds", justify="right")
        t.add_column("share", justify="right")
        t.add_column("calls", justify="right")
        for r in run["phases"]:
            calls = "" if r["calls"] is None else str(r["calls"])
            t.add_row(r["phase"], f"{r['seconds']:.3f}", f"{r['share']:.0%}", calls)
        con.print(t)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", help="Vault path (or the VAULT_PATH env var)")
    ap.add_argument("--embedder", choices=["fastembed", "hash"], help="Override VAULTSEARCH_EMBEDDER")
    ap.add_argument("--embed-batch", type=int, help="Texts per ONNX run (VAULTSEARCH_EMBED_BATCH)")
    ap.add_argument("--threads", type=int, help="ONNX intra-op threads (VAULTSEARCH_EMBED_THREADS)")
    ap.add_argument("--index-batch", type=int,
                    help="Chunks gathered across notes per embed call (VAULTSEARCH_INDEX_BATCH)")
    ap.add_argument("--json", action="store_true", help="Also print the raw results as JSON on stdout")
    args = ap.parse_args(argv)

    # Per-note INFO logs would add their own cost to the measurement
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="vaultsearch-bench-") as tmp:
        cfg = Config.from_env(args.vault, db=str(Path(tmp) / "bench.db"))
        if args.embedder:
            cfg = replace(cfg, embedder=args.embedder)
        if args.embed_batch:
            cfg = replace(cfg, embed_batch_size=args.embed_batch)
        if args.threads:
            cfg = replace(cfg, embed_threads=args.threads)
        if args.index_batch:
            cfg = replace(cfg, index_batch_chunks=args.index_batch)
        res = benchmark(cfg)

    _print_report(res)
    if args.json:
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
