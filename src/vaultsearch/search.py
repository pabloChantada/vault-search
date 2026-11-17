"""Búsqueda híbrida: BM25 (FTS5) + vectores, fusionados con Reciprocal Rank Fusion."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .embedder import Embedder
from .store import Store

RRF_K = 60
WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class Hit:
    path: str
    title: str
    heading: str
    text: str
    score: float
    tags: list[str]

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "title": self.title,
            "heading": self.heading,
            "text": self.text,
            "score": round(self.score, 4),
            "tags": self.tags,
        }


def _fts_query(q: str) -> str:
    # Cada palabra entre comillas (evita errores de sintaxis FTS5) y con prefijo*, unidas con OR
    words = [w for w in WORD_RE.findall(q) if len(w) > 1]
    return " OR ".join(f'"{w}"*' for w in words)


class Searcher:
    def __init__(self, store: Store, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    def search(self, query: str, limit: int = 8, folder: str | None = None, tag: str | None = None) -> list[Hit]:
        pool = max(limit * 5, 40) if (folder or tag) else max(limit * 3, 20)
        ranks: dict[int, float] = {}

        fts_expr = _fts_query(query)
        if fts_expr:
            for rank, (cid, _) in enumerate(self.store.fts_search(fts_expr, pool)):
                ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (RRF_K + rank)

        emb = self.embedder.embed([query])[0]
        for rank, (cid, _) in enumerate(self.store.vec_search(emb, pool)):
            ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (RRF_K + rank)

        rows = self.store.chunks_by_ids(list(ranks))
        hits: list[Hit] = []
        per_note: dict[str, int] = {}
        prefix = folder.strip("/") + "/" if folder else None
        tag = tag.lstrip("#").lower() if tag else None
        for cid in sorted(ranks, key=ranks.get, reverse=True):
            r = rows.get(cid)
            if r is None:
                continue
            tags = json.loads(r["tags"])
            if prefix and not r["path"].startswith(prefix):
                continue
            if tag and tag not in (t.lower() for t in tags):
                continue
            # Máximo 2 trozos por nota para que salgan notas variadas
            if per_note.get(r["path"], 0) >= 2:
                continue
            per_note[r["path"]] = per_note.get(r["path"], 0) + 1
            hits.append(Hit(r["path"], r["title"], r["heading"], r["text"], ranks[cid], tags))
            if len(hits) >= limit:
                break
        return hits
