"""The benchmark script lives outside the package, so load it by path."""
import importlib.util
from pathlib import Path

from vaultsearch.core import Config
from vaultsearch.core import indexer as indexer_mod

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_index.py"


def _load_bench():
    spec = importlib.util.spec_from_file_location("bench_index", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_benchmark_reports_cold_and_warm_runs(vault, tmp_path):
    bench = _load_bench()
    original_parse = indexer_mod.parse_note
    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "bench.db", embedder="hash")

    res = bench.benchmark(cfg)

    assert res["notes"] == 2
    assert res["cold"]["counts"]["new"] == 2
    assert res["cold"]["counters"]["texts embedded"] == res["chunks"]
    assert {p["phase"] for p in res["cold"]["phases"]} >= {"embed", "sqlite write", "parse + chunk"}
    assert abs(sum(p["seconds"] for p in res["cold"]["phases"]) - res["cold"]["seconds"]) < 1e-6
    # The warm sync opens nothing, so nothing is parsed or embedded
    assert res["warm"]["counts"]["unchanged"] == 2
    assert "embed" not in {p["phase"] for p in res["warm"]["phases"]}
    assert indexer_mod.parse_note is original_parse  # patches are undone
