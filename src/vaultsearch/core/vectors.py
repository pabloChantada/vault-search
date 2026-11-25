"""Exact nearest-neighbour search over every chunk embedding, held in memory.

sqlite-vec stays the store of record, but its brute-force scan took ~12 ms per query on a
6,500-chunk vault. The same exact search as one float32 matrix-vector product takes
~0.2 ms, for ~1.5 KB of RAM per chunk (10 MB for that vault).

An approximate index (IVF, HNSW) would only start to pay off around 100k chunks, and it
would trade away exactness for it.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np


def _normalize(m: np.ndarray) -> np.ndarray:
    """Scale rows to unit length so a dot product is the cosine similarity."""
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.maximum(norms, 1e-12)


class VectorIndex:
    """Unit-normalised embeddings in one growable matrix, with O(1) add and remove.

    Rows [0, n) are live. Removing a row moves the last live row into its slot, so the
    matrix never has holes and a search is a single product over a contiguous block.
    """

    def __init__(self, dim: int, capacity: int = 1024) -> None:
        self.dim = dim
        self._m = np.empty((capacity, dim), dtype=np.float32)
        self._ids = np.empty(capacity, dtype=np.int64)
        self._row: dict[int, int] = {}  # chunk id -> row
        self._n = 0

    @classmethod
    def from_rows(cls, dim: int, rows: Iterable[tuple[int, bytes]]) -> VectorIndex:
        """Build from (chunk id, packed float32 embedding) pairs, as stored in sqlite-vec."""
        rows = list(rows)
        index = cls(dim, capacity=max(len(rows), 1024))
        if rows:
            n = len(rows)
            index._ids[:n] = [cid for cid, _ in rows]
            blob = b"".join(emb for _, emb in rows)
            index._m[:n] = _normalize(np.frombuffer(blob, dtype=np.float32).reshape(n, dim))
            index._row = {cid: i for i, (cid, _) in enumerate(rows)}
            index._n = n
        return index

    def __len__(self) -> int:
        return self._n

    def __contains__(self, cid: int) -> bool:
        return cid in self._row

    def add(self, cid: int, emb: Iterable[float]) -> None:
        if cid in self._row:
            self.remove(cid)
        if self._n == len(self._m):
            self._grow()
        i = self._n
        self._m[i] = _normalize(np.asarray(emb, dtype=np.float32))
        self._ids[i] = cid
        self._row[cid] = i
        self._n += 1

    def remove(self, cid: int) -> None:
        i = self._row.pop(cid, None)
        if i is None:
            return
        last = self._n - 1
        if i != last:
            moved = int(self._ids[last])
            self._m[i] = self._m[last]
            self._ids[i] = moved
            self._row[moved] = i
        self._n = last

    def search(self, emb: Iterable[float], k: int) -> list[tuple[int, float]]:
        """The k nearest chunks as (chunk id, cosine distance), nearest first.

        Distances are 1 - cosine similarity, the same values sqlite-vec reports. Ties go to
        the lower id, so the order doesn't depend on where removals moved rows.
        """
        k = min(k, self._n)
        if k <= 0:
            return []
        q = _normalize(np.asarray(emb, dtype=np.float32))
        sims = self._m[: self._n] @ q
        top = np.argpartition(-sims, k - 1)[:k] if k < self._n else np.arange(self._n)
        top = top[np.lexsort((self._ids[top], -sims[top]))]
        return [(int(self._ids[i]), float(1.0 - sims[i])) for i in top]

    def _grow(self) -> None:
        cap = 2 * len(self._m)
        m = np.empty((cap, self.dim), dtype=np.float32)
        ids = np.empty(cap, dtype=np.int64)
        m[: self._n] = self._m[: self._n]
        ids[: self._n] = self._ids[: self._n]
        self._m, self._ids = m, ids
