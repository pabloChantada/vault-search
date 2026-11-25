import json

import numpy as np

from vaultsearch.viz import build_graph, project, write_html


def test_project_pca_is_3d_and_bounded():
    vecs = np.random.default_rng(0).normal(size=(10, 32)).astype(np.float32)
    coords, method = project(vecs, method="pca")
    assert method == "pca"
    assert coords.shape == (10, 3)
    assert np.abs(coords).max() <= 1.0 + 1e-6


def test_project_2d():
    vecs = np.random.default_rng(1).normal(size=(12, 16)).astype(np.float32)
    coords, _ = project(vecs, method="pca", dims=2)
    assert coords.shape == (12, 2) and np.abs(coords).max() <= 1.0 + 1e-6


def test_project_handles_tiny_inputs():
    assert project(np.zeros((0, 8), dtype=np.float32), "pca")[0].shape == (0, 3)
    assert project(np.ones((1, 8), dtype=np.float32), "pca")[0].shape == (1, 3)


def test_graph_has_one_node_per_note_and_resolves_links(engine):
    engine.open()
    engine.indexer.sync_all()
    g = build_graph(engine.store, method="pca")
    by_path = {n["path"]: i for i, n in enumerate(g["nodes"])}
    assert set(by_path) == {"Japanese/Kanji.md", "Journal/2026-11-17.md"}
    assert {n["folder"] for n in g["nodes"]} == {"Japanese", "Journal"}
    assert all(n["mtime"] > 0 and n["created"] > 0 and len(n["pos2"]) == 2 for n in g["nodes"])
    # The journal entry links [[Kanji]]
    assert g["links"] == [tuple(sorted((by_path["Japanese/Kanji.md"], by_path["Journal/2026-11-17.md"])))]


def test_write_html_embeds_the_data(engine, tmp_path):
    engine.open()
    engine.indexer.sync_all()
    out = write_html(build_graph(engine.store, "pca"), tmp_path / "map.html", "SecondBrain", "sansui")
    html = out.read_text(encoding="utf-8")
    assert "/*__DATA__*/" not in html
    payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    data = json.loads(payload.replace("<\\/", "</"))
    assert data["vault"] == "SecondBrain" and len(data["nodes"]) == 2


def test_fingerprint_follows_chunks_not_metadata(engine, vault):
    engine.open()
    engine.indexer.sync_all()
    before = engine.store.embedding_fingerprint()
    note = vault / "Journal" / "2026-11-17.md"
    engine.indexer.index_file(note)  # nothing changed
    assert engine.store.embedding_fingerprint() == before
    note.write_text("# Tuesday\nA different day entirely.\n", encoding="utf-8")
    engine.indexer.index_file(note)
    assert engine.store.embedding_fingerprint() != before


def test_layout_cache_skips_the_projection_until_notes_change(engine, vault, tmp_path, monkeypatch):
    from vaultsearch.viz import map3d

    engine.open()
    engine.indexer.sync_all()
    cache = tmp_path / "layout.json"
    calls = []
    real = map3d.project
    monkeypatch.setattr(map3d, "project", lambda *a, **k: calls.append(1) or real(*a, **k))

    first = build_graph(engine.store, "pca", cache=cache)
    assert len(calls) == 2 and cache.is_file()  # 3D + ground plane
    again = build_graph(engine.store, "pca", cache=cache)
    assert len(calls) == 2, "a fresh cache must not re-project"
    assert again["nodes"] == first["nodes"] and again["method"] == "pca"

    build_graph(engine.store, "auto", cache=cache)  # another method is another layout
    assert len(calls) == 4

    (vault / "Japanese" / "Kana.md").write_text("# Kana\nHiragana and katakana.\n", encoding="utf-8")
    engine.indexer.sync_all()
    g = build_graph(engine.store, "auto", cache=cache)
    assert len(calls) == 6 and len(g["nodes"]) == 3


def test_broken_layout_cache_is_recomputed(engine, tmp_path):
    engine.open()
    engine.indexer.sync_all()
    cache = tmp_path / "layout.json"
    cache.write_text("{not json", encoding="utf-8")
    assert len(build_graph(engine.store, "pca", cache=cache)["nodes"]) == 2
    assert json.loads(cache.read_text(encoding="utf-8"))["requested"] == "pca"


def test_seed_keeps_known_notes_and_places_new_ones_by_their_neighbour():
    from vaultsearch.viz.map3d import _seed

    m = np.eye(3, dtype=np.float32)
    m = np.vstack([m, [0.9, 0.1, 0.0]])  # a new note, closest to the first
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    prev = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [-1.0, 0.0]}
    init = _seed(m, ["a", "b", "c", "new"], prev, dims=2)
    assert np.allclose(init[:3], [[1, 0], [0, 1], [-1, 0]])
    assert np.linalg.norm(init[3] - init[0]) < 0.1
    assert _seed(m, ["w", "x", "y", "a"], prev, dims=2) is None  # mostly unknown: start fresh


def test_align_undoes_a_mirrored_and_rotated_refit():
    from vaultsearch.viz.map3d import _align

    old = np.random.default_rng(2).uniform(-0.5, 0.5, size=(20, 2)).astype(np.float32)
    turn = np.array([[0.0, -1.0], [-1.0, 0.0]], dtype=np.float32)  # rotation + mirror
    refit = old @ turn * 1.7 + 0.3
    paths = [f"n{i}" for i in range(20)]
    aligned = _align(refit, paths, {p: list(c) for p, c in zip(paths, old)})
    assert np.allclose(aligned, old, atol=1e-4)
    assert np.abs(aligned).max() <= 1.0


def test_every_theme_renders_and_unknown_ones_fail(engine, tmp_path):
    import pytest

    from vaultsearch.viz import available_themes

    engine.open()
    engine.indexer.sync_all()
    g = build_graph(engine.store, "pca")
    assert set(available_themes()) == {"sansui", "yggdrasil"}
    for theme in available_themes():
        html = write_html(g, tmp_path / f"{theme}.html", "SecondBrain", theme).read_text(encoding="utf-8")
        assert "/*__DATA__*/" not in html
    with pytest.raises(ValueError):
        write_html(g, tmp_path / "x.html", "SecondBrain", "galaxy")
