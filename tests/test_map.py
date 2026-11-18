import json

import numpy as np

from vaultsearch.viz import build_graph, project, write_html


def test_project_pca_is_3d_and_bounded():
    vecs = np.random.default_rng(0).normal(size=(10, 32)).astype(np.float32)
    coords, method = project(vecs, method="pca")
    assert method == "pca"
    assert coords.shape == (10, 3)
    assert np.abs(coords).max() <= 1.0 + 1e-6


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
    # The journal entry links [[Kanji]]
    assert g["links"] == [tuple(sorted((by_path["Japanese/Kanji.md"], by_path["Journal/2026-11-17.md"])))]


def test_write_html_embeds_the_data(engine, tmp_path):
    engine.open()
    engine.indexer.sync_all()
    out = write_html(build_graph(engine.store, "pca"), tmp_path / "map.html", "SecondBrain")
    html = out.read_text(encoding="utf-8")
    assert "/*__DATA__*/" not in html
    payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    data = json.loads(payload.replace("<\\/", "</"))
    assert data["vault"] == "SecondBrain" and len(data["nodes"]) == 2
