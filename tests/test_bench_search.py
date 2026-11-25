"""The search benchmark lives outside the package, so load it by path."""
import importlib.util
from pathlib import Path

from vaultsearch.core import Config

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_search.py"


def _load_bench():
    spec = importlib.util.spec_from_file_location("bench_search", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_percentile_is_nearest_rank():
    bench = _load_bench()
    values = [float(v) for v in range(1, 101)]
    assert bench.percentile(values, 50) == 50
    assert bench.percentile(values, 95) == 95
    assert bench.percentile(values, 100) == 100
    assert bench.percentile([7.0], 99) == 7
    assert bench.percentile([], 50) == 0


def test_benchmark_builds_an_index_and_times_every_phase(vault, tmp_path):
    bench = _load_bench()
    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "bench.db", embedder="hash")
    queries = [("keyword", "kanji"), ("natural", "talked to the recruiter")]

    res = bench.benchmark(cfg, queries, repeat=3, limit=5)

    assert res["index_built"] and res["notes"] == 2
    assert res["hits"]["kanji"] >= 1
    assert {p["phase"] for p in res["phases"]} == set(bench.PHASES)
    assert set(res["categories"]) == {"keyword", "natural"}
    assert abs(sum(p["share"] for p in res["phases"]) - 1) < 1e-6
    assert res["total"]["p50_ms"] <= res["total"]["p95_ms"] <= res["total"]["max_ms"]


def test_benchmark_copies_an_existing_index_without_touching_it(vault, tmp_path):
    bench = _load_bench()
    src_cfg = Config(vault=vault.resolve(), db_path=tmp_path / "real.db", embedder="hash")
    bench.benchmark(src_cfg, [("keyword", "kanji")], repeat=1)  # builds the "real" index
    before = src_cfg.db_path.stat().st_mtime_ns

    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "copy.db", embedder="hash")
    res = bench.benchmark(cfg, [("keyword", "kanji")], repeat=1, source_db=src_cfg.db_path)

    assert not res["index_built"]
    assert res["chunks"] > 0
    assert src_cfg.db_path.stat().st_mtime_ns == before
