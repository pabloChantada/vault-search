from pathlib import Path

from vaultsearch.core.embedder import FastEmbedder


class _FakeModel:
    """Stands in for fastembed: the 'vector' of a text is just its length."""

    def __init__(self):
        self.seen: list[str] = []

    def embed(self, texts, batch_size):
        self.seen = list(texts)
        for t in texts:
            yield _Vec([float(len(t))])


class _Vec(list):
    def tolist(self):
        return list(self)


def test_fast_embedder_sorts_by_length_and_restores_order():
    emb = FastEmbedder.__new__(FastEmbedder)  # skip loading the real model
    emb._model, emb.batch_size, emb.dim = _FakeModel(), 2, 1
    texts = ["ccc", "a", "bb", "dddd"]
    assert emb.embed(texts) == [[3.0], [1.0], [2.0], [4.0]]
    assert emb._model.seen == ["a", "bb", "ccc", "dddd"]


# ---------- OnnxEmbedder: the default model run from fastembed's cache ----------
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from vaultsearch.core import embedder as embedder_mod  # noqa: E402
from vaultsearch.core.config import DEFAULT_MODEL  # noqa: E402
from vaultsearch.core.embedder import (  # noqa: E402
    ONNX_MODELS,
    _sorted_batches,
    find_cached_model,
    make_embedder,
    mean_pool,
)


def test_sorted_batches_groups_similar_lengths_and_restores_order():
    batches = []

    def run(batch):
        batches.append(batch)
        return [np.array([float(len(t))]) for t in batch]

    texts = ["ccc", "a", "eeeee", "bb", "dddd"]
    assert _sorted_batches(texts, 2, run) == [[3.0], [1.0], [5.0], [2.0], [4.0]]
    assert batches == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]


def test_mean_pool_skips_padding():
    hidden = np.array([[[1.0, 1.0], [3.0, 3.0], [100.0, 100.0]]], dtype=np.float32)
    mask = np.array([[1, 1, 0]])
    pooled = mean_pool(hidden, mask)
    assert pooled.dtype == np.float32
    assert pooled.tolist() == [[2.0, 2.0]]


def _snapshot(cache: Path, name: str, files=("model_optimized.onnx", "tokenizer.json", "tokenizer_config.json")):
    repo, _ = ONNX_MODELS[DEFAULT_MODEL]
    d = cache / f"models--{repo.replace('/', '--')}" / "snapshots" / name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_text("{}", encoding="utf-8")
    return d


def test_find_cached_model_takes_a_complete_snapshot(tmp_path):
    assert find_cached_model(DEFAULT_MODEL, tmp_path) is None
    _snapshot(tmp_path, "partial", files=("tokenizer.json",))  # a download cut short
    assert find_cached_model(DEFAULT_MODEL, tmp_path) is None
    full = _snapshot(tmp_path, "full")
    assert find_cached_model(DEFAULT_MODEL, tmp_path) == (full, "model_optimized.onnx")
    assert find_cached_model("some/other-model", tmp_path) is None


class _StubFastEmbedder:
    def __init__(self, model, batch_size=8, threads=None):
        self.model, self.dim = model, 3


def test_make_embedder_uses_fastembed_when_the_model_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.setattr(embedder_mod, "FastEmbedder", _StubFastEmbedder)
    assert isinstance(make_embedder("fastembed", DEFAULT_MODEL), _StubFastEmbedder)


def test_make_embedder_falls_back_to_fastembed_on_a_broken_snapshot(tmp_path, monkeypatch, caplog):
    _snapshot(tmp_path, "broken")  # files exist but hold no model
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.setattr(embedder_mod, "FastEmbedder", _StubFastEmbedder)
    assert isinstance(make_embedder("fastembed", DEFAULT_MODEL), _StubFastEmbedder)
    assert "falling back to fastembed" in caplog.text


@pytest.mark.skipif(find_cached_model(DEFAULT_MODEL) is None, reason="the model isn't downloaded")
def test_onnx_embedder_gives_fastembeds_vectors():
    texts = ["grafos", "日本語の勉強", "How was I studying?", "una frase " * 200]  # the last is truncated
    ours = embedder_mod.OnnxEmbedder(*find_cached_model(DEFAULT_MODEL), batch_size=2)
    theirs = FastEmbedder(DEFAULT_MODEL, batch_size=2)
    assert ours.dim == theirs.dim == 384
    np.testing.assert_allclose(ours.embed(texts), theirs.embed(texts), atol=1e-5)
