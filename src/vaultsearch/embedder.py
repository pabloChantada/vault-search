"""Embeddings locales con fastembed (ONNX, sin torch, funciona en CPU).

El modelo por defecto es multilingüe (español, inglés, japonés...). La primera
vez descarga ~220 MB de HuggingFace y luego queda en caché.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    def __init__(self, model: str):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model)
        self.dim = len(next(iter(self._model.embed(["dim"]))))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts, batch_size=32)]


class HashEmbedder:
    """Embedder falso y determinista (bolsa de palabras con hashing). Solo para tests sin red."""

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


def make_embedder(kind: str, model: str) -> Embedder:
    if kind == "hash":
        return HashEmbedder()
    return FastEmbedder(model)
