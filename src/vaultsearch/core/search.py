"""Hybrid search: BM25 (FTS5) + vector similarity, merged with Reciprocal Rank Fusion."""
from __future__ import annotations

import json
import re
import unicodedata
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


def _fold(word: str) -> str:
    """Lowercase and strip diacritics, as the FTS5 tokenizer does to the terms it indexes."""
    return "".join(c for c in unicodedata.normalize("NFKD", word.lower()) if not unicodedata.combining(c))


def _fts_query(q: str, common: frozenset[str] = frozenset()) -> str:
    """An FTS5 expression for `q`: every word quoted (no syntax errors), as a prefix, ORed.

    Prefixes stand in for stemming ("árbol" finds "árboles"). Common words are dropped:
    their IDF is close to 0, so they barely move the ranking, yet FTS5 has to score every
    row they match ("de" is in 87% of the chunks of a Spanish vault, and made long
    questions take 30-40 ms instead of 1-2 ms). If every word is common, they're all kept.
    """
    words = list({_fold(w): w for w in WORD_RE.findall(q) if len(w) > 1}.values())
    words = [w for w in words if _fold(w) not in common] or words
    return " OR ".join(f'"{w}"*' for w in words)


class Searcher:
    def __init__(self, store: Store, embedder: Embedder):
        self.store = store
        self.embedder = embedder

    def search(self, query: str, limit: int = 8, folder: str | None = None, tag: str | None = None) -> list[Hit]:
        pool = max(limit * 5, 40) if (folder or tag) else max(limit * 3, 20)
        ranks: dict[int, float] = {}

        fts_expr = _fts_query(query, self.store.common_terms())
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
            # At most 2 chunks per note so results stay varied
            if per_note.get(r["path"], 0) >= 2:
                continue
            per_note[r["path"]] = per_note.get(r["path"], 0) + 1
            hits.append(Hit(r["path"], r["title"], r["heading"], r["text"], ranks[cid], tags))
            if len(hits) >= limit:
                break
        return hits
