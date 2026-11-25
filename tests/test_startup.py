"""Startup: keyword search works before the model has loaded, and nothing heavy is imported early."""
import subprocess
import sys
import threading

from vaultsearch.core import Config, Engine
from vaultsearch.core import embedder as embedder_mod
from vaultsearch.core.store import Store


def _cfg(vault, tmp_path):
    return Config(vault=vault.resolve(), db_path=tmp_path / "index.db", embedder="hash")


def test_existing_dim_only_trusts_an_index_it_can_use_as_is(vault, tmp_path):
    cfg = _cfg(vault, tmp_path)
    assert Store.existing_dim(cfg.db_path, "hash") is None  # no index yet
    Engine(cfg).start(watch=False, background=False)

    assert Store.existing_dim(cfg.db_path, "hash") == 256
    assert Store.existing_dim(cfg.db_path, "another-model") is None  # opening it would rebuild
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database")
    assert Store.existing_dim(garbage, "hash") is None


def test_keyword_search_works_while_the_model_loads(vault, tmp_path, monkeypatch):
    cfg = _cfg(vault, tmp_path)
    Engine(cfg).start(watch=False, background=False)  # a previous run built the index

    release = threading.Event()
    real = embedder_mod.make_embedder

    def slow_make_embedder(*args, **kwargs):
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(embedder_mod, "make_embedder", slow_make_embedder)
    engine = Engine(cfg)
    opener = threading.Thread(target=engine.open)
    opener.start()
    try:
        assert engine.keywords_ready.wait(10)
        assert not engine.ready.is_set()
        assert not engine.searcher.semantic
        hits = engine.searcher.search("kanji", limit=3)
        assert hits and hits[0].path == "Japanese/Kanji.md"
    finally:
        release.set()
        opener.join(10)

    assert engine.ready.is_set() and engine.searcher.semantic
    assert engine.indexer is not None and engine.indexer.store is engine.store


def test_a_first_run_waits_for_the_model_before_opening_the_index(vault, tmp_path):
    engine = Engine(_cfg(vault, tmp_path))
    engine.open()
    assert engine.ready.is_set() and engine.keywords_ready.is_set()
    assert engine.searcher.semantic


def test_the_tui_draws_before_importing_the_engines_dependencies():
    # A fresh interpreter, so modules the other tests imported don't count
    code = (
        "import sys, vaultsearch.tui.app; "
        "print(','.join(m for m in ('numpy', 'sqlite_vec', 'onnxruntime', 'watchdog', 'fastembed') "
        "if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
