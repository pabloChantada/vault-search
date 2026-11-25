"""The in-memory vector index must give exactly the answers sqlite-vec would."""
import numpy as np
import pytest

from vaultsearch.core import Config, Engine
from vaultsearch.core.store import _pack
from vaultsearch.core.vectors import VectorIndex

DIM = 16


def _brute_force(vecs: dict[int, np.ndarray], q: np.ndarray, k: int) -> list[int]:
    unit = lambda v: v / np.linalg.norm(v)  # noqa: E731
    sims = {cid: float(unit(v) @ unit(q)) for cid, v in vecs.items()}
    return sorted(sims, key=lambda c: (-sims[c], c))[:k]


def test_search_matches_brute_force_through_adds_removes_and_growth():
    rng = np.random.default_rng(0)
    index = VectorIndex(DIM, capacity=4)  # small, so adding has to grow the matrix
    vecs: dict[int, np.ndarray] = {}
    for cid in range(1, 51):
        vecs[cid] = rng.normal(size=DIM).astype(np.float32) * rng.uniform(0.5, 5)  # not unit length
        index.add(cid, vecs[cid])
    for cid in (1, 7, 50, 23):  # first, middle, last (no swap needed) and another
        index.remove(cid)
        del vecs[cid]
    vecs[7] = rng.normal(size=DIM).astype(np.float32)  # an id can come back
    index.add(7, vecs[7])

    assert len(index) == len(vecs) == 47
    for _ in range(10):
        q = rng.normal(size=DIM).astype(np.float32)
        got = index.search(q, 10)
        assert [cid for cid, _ in got] == _brute_force(vecs, q, 10)
        dists = [d for _, d in got]
        assert dists == sorted(dists) and all(0 <= d <= 2 for d in dists)


def test_search_edge_cases():
    index = VectorIndex(DIM)
    assert index.search(np.ones(DIM), 5) == []
    index.add(3, np.ones(DIM))
    index.remove(99)  # unknown ids are ignored
    assert index.search(np.ones(DIM), 5) == [(3, pytest.approx(0.0, abs=1e-6))]
    index.add(3, -np.ones(DIM))  # re-adding replaces
    assert len(index) == 1
    assert index.search(np.ones(DIM), 1)[0][1] == pytest.approx(2.0)


def test_from_rows_reads_packed_sqlite_vec_blobs():
    rows = [(10, _pack([1.0] + [0.0] * (DIM - 1))), (20, _pack([0.0, 3.0] + [0.0] * (DIM - 2)))]
    index = VectorIndex.from_rows(DIM, rows)
    assert 10 in index and 20 in index
    assert [cid for cid, _ in index.search([0.0, 1.0] + [0.0] * (DIM - 2), 2)] == [20, 10]


def _sqlite_vec_ids(store, emb, k):
    rows = store.db.execute(
        "SELECT rowid, distance FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (_pack(emb), k),
    ).fetchall()
    # sqlite-vec leaves ties in storage order; VectorIndex breaks them by id
    return [cid for _, cid in sorted((round(r["distance"], 5), r["rowid"]) for r in rows)]


def _assert_in_step(engine):
    store, embedder = engine.store, engine.searcher.embedder
    for q in ("kanji rain", "recruiter offer", "sun day", "nothing like this"):
        emb = embedder.embed([q])[0]
        assert [cid for cid, _ in store.vec_search(emb, 10)] == _sqlite_vec_ids(store, emb, 10)


def test_store_keeps_memory_and_sqlite_vec_in_step(vault, tmp_path):
    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "index.db", embedder="hash")
    engine = Engine(cfg)
    engine.start(watch=False, background=False)
    _assert_in_step(engine)

    # Edit, add, rename and delete notes: every write path must update the in-memory copy
    kanji = vault / "Japanese" / "Kanji.md"
    kanji.write_text(kanji.read_text(encoding="utf-8") + "\n## N3\nThe kanji 雪 means snow.\n", encoding="utf-8")
    (vault / "Journal" / "2026-11-18.md").write_text("# Wednesday\nRain all day, studied kanji.\n", encoding="utf-8")
    engine.indexer.sync_all()
    _assert_in_step(engine)

    engine.store.rename_file("Journal/2026-11-17.md", "Journal/tuesday.md")
    engine.store.delete_file("Journal/2026-11-18.md")
    _assert_in_step(engine)
    assert len(engine.store._vectors) == engine.store.stats()["chunks"]


def test_a_rolled_back_write_drops_the_memory_copy(vault, tmp_path):
    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "index.db", embedder="hash")
    engine = Engine(cfg)
    engine.start(watch=False, background=False)
    store = engine.store
    assert store._vectors is not None

    with pytest.raises(RuntimeError), store._writing():
        store.delete_file("Japanese/Kanji.md")
        raise RuntimeError("simulated failure mid-transaction")

    assert store._vectors is None  # the deletion was rolled back in SQLite, not in memory
    _assert_in_step(engine)  # reloaded from sqlite-vec on the next search
    assert len(store._vectors) == store.stats()["chunks"]
