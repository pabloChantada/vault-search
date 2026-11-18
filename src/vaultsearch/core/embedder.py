"""Local embeddings with fastembed (ONNX, no torch, runs on CPU).

The default model is multilingual (English, Spanish, Japanese, ...). The first
run downloads ~220 MB from Hugging Face; after that it is cached.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    def __init__(self, model: str, batch_size: int = 8, threads: int | None = None):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model, threads=threads)
        self.batch_size = batch_size
        self.dim = len(next(iter(self._model.embed(["dim"]))))

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Each ONNX batch is padded to its longest text, so feeding the texts sorted by
        # length means short chunks no longer pay for a long neighbour.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vecs = self._model.embed([texts[i] for i in order], batch_size=self.batch_size)
        out: list[list[float]] = [[] for _ in texts]
        for i, v in zip(order, vecs):
            out[i] = v.tolist()
        return out


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
    return FastEmbedder(model, batch_size=batch_size, threads=threads)
