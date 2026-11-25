"""Local embeddings on CPU with ONNX: no torch, no API key.

The default model is multilingual (English, Spanish, Japanese, ...). fastembed downloads
it on the first run (~220 MB from Hugging Face) and caches it. From then on, the model is
run straight from that cache with onnxruntime and tokenizers (`OnnxEmbedder`): importing
fastembed alone takes ~1.7 s, mostly for its image models and HTTP clients, which is more
than the rest of the model load put together.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

log = logging.getLogger("vaultsearch")


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _sorted_batches(texts: list[str], batch_size: int, run: Callable[[list[str]], list]) -> list[list[float]]:
    """Embed `texts` in batches of similar length, and return the vectors in input order.

    Each ONNX batch is padded to its longest text, so feeding the texts sorted by length
    means short chunks no longer pay for a long neighbour.
    """
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out: list[list[float]] = [[] for _ in texts]
    for start in range(0, len(order), batch_size):
        batch = order[start : start + batch_size]
        for i, v in zip(batch, run([texts[i] for i in batch])):
            out[i] = v.tolist()
    return out


class FastEmbedder:
    """Any fastembed model. Used on the first run, to download the model, and for models
    `OnnxEmbedder` doesn't know."""

    def __init__(self, model: str, batch_size: int = 8, threads: int | None = None):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model, threads=threads)
        self.batch_size = batch_size
        self.dim = len(next(iter(self._model.embed(["dim"]))))

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Sorted by length for the same reason as _sorted_batches, in one call to fastembed
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vecs = self._model.embed([texts[i] for i in order], batch_size=self.batch_size)
        out: list[list[float]] = [[] for _ in texts]
        for i, v in zip(order, vecs):
            out[i] = v.tolist()
        return out


# Models OnnxEmbedder can run: name -> (Hugging Face repo fastembed downloads, ONNX file).
# Every one of them is mean-pooled, as fastembed's PooledEmbedding does.
ONNX_MODELS = {
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": (
        "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
        "model_optimized.onnx",
    ),
}


def fastembed_cache_dir() -> Path:
    """Where fastembed keeps its models (its own default, unless FASTEMBED_CACHE_PATH is set)."""
    return Path(os.environ.get("FASTEMBED_CACHE_PATH", Path(tempfile.gettempdir()) / "fastembed_cache"))


def find_cached_model(model: str, cache_dir: Path | None = None) -> tuple[Path, str] | None:
    """The newest complete snapshot of `model` in the fastembed cache, and its ONNX file name."""
    if model not in ONNX_MODELS:
        return None
    repo, onnx_file = ONNX_MODELS[model]
    snapshots = (cache_dir or fastembed_cache_dir()) / f"models--{repo.replace('/', '--')}" / "snapshots"
    needed = (onnx_file, "tokenizer.json", "tokenizer_config.json")
    complete = [d for d in snapshots.glob("*") if all((d / f).is_file() for f in needed)]
    if not complete:
        return None
    return max(complete, key=lambda d: (d / onnx_file).stat().st_mtime), onnx_file


def _context_length(tokenizer_config: dict) -> int:
    """The stricter of the two limits a tokenizer config may give, ignoring placeholders
    (transformers writes 1e30 when it doesn't know)."""
    limits = [
        v for v in (tokenizer_config.get("model_max_length"), tokenizer_config.get("max_length"))
        if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= sys.maxsize
    ]
    if not limits:
        raise ValueError("tokenizer_config.json gives no usable maximum length")
    return min(limits)


def load_tokenizer(model_dir: Path):
    """The tokenizer set up as fastembed sets it up: truncated to the model's context,
    padded to the longest text of each batch, special tokens registered."""
    from tokenizers import AddedToken, Tokenizer

    config = json.loads((model_dir / "tokenizer_config.json").read_text(encoding="utf-8"))
    model_config = model_dir / "config.json"
    model_config = json.loads(model_config.read_text(encoding="utf-8")) if model_config.is_file() else {}
    special = model_dir / "special_tokens_map.json"
    special = json.loads(special.read_text(encoding="utf-8")) if special.is_file() else {}

    tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    tok.enable_truncation(
        max_length=_context_length(config),
        direction=config.get("truncation_side") or (tok.truncation or {}).get("direction", "right"),
    )
    for value in special.values():
        for token in value if isinstance(value, list) else [value]:
            tok.add_special_tokens([token if isinstance(token, str) else AddedToken(**token)])

    padding = tok.padding or {}
    pad_token = padding.get("pad_token") or config.get("pad_token")
    pad_id = padding.get("pad_id", model_config.get("pad_token_id"))
    if pad_id is None and pad_token is not None:
        pad_id = tok.token_to_id(pad_token)
    if pad_token is None or pad_id is None:
        raise ValueError(f"No pad token for {model_dir}")
    tok.enable_padding(
        direction=config.get("padding_side") or padding.get("direction", "right"),
        pad_id=pad_id,
        pad_type_id=padding.get("pad_type_id", 0),
        pad_token=pad_token,
        pad_to_multiple_of=padding.get("pad_to_multiple_of"),
        length=None,
    )
    return tok


def mean_pool(hidden, attention_mask):
    """Average each text's token vectors, skipping the padding (summed in float64, as fastembed does)."""
    import numpy as np

    summed = np.sum(hidden, axis=1, where=attention_mask[:, :, None].astype(bool), dtype=np.float64)
    counts = np.maximum(attention_mask.sum(axis=1, keepdims=True), 1e-9)
    return (summed / counts).astype(hidden.dtype, copy=False)


class OnnxEmbedder:
    """A fastembed model run from its cache with onnxruntime and tokenizers, without fastembed.

    The vectors are the same as fastembed's, so an index built by either works with both.
    """

    def __init__(self, model_dir: Path, onnx_file: str, batch_size: int = 8, threads: int | None = None):
        import onnxruntime as ort

        self.tokenizer = load_tokenizer(model_dir)
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads is not None:
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = threads
        self.session = ort.InferenceSession(
            str(model_dir / onnx_file), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self.session.get_inputs()}
        self.batch_size = batch_size
        self.dim = len(self._run(["dim"])[0])

    def _run(self, texts: list[str]):
        import numpy as np

        encoded = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed = {"input_ids": ids}
        if "attention_mask" in self._inputs:
            feed["attention_mask"] = mask
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feed)[0]
        return mean_pool(hidden, mask)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return _sorted_batches(texts, self.batch_size, self._run)


class HashEmbedder:
    """Fake, deterministic embedder (hashed bag of words). For tests without network access."""

    dim = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in t.lower().split():
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def make_embedder(kind: str, model: str, batch_size: int = 8, threads: int | None = None) -> Embedder:
    if kind == "hash":
        return HashEmbedder()
    if cached := find_cached_model(model):
        try:
            return OnnxEmbedder(*cached, batch_size=batch_size, threads=threads)
        except Exception:  # an incomplete or newer-format snapshot: let fastembed deal with it
            log.warning("Could not load %s from %s, falling back to fastembed", model, cached[0], exc_info=True)
    return FastEmbedder(model, batch_size=batch_size, threads=threads)
