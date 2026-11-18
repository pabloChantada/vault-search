"""Project note embeddings to 3D and render them as a self-contained three.js page.

Each note is the mean of its chunk embeddings. UMAP (cosine) keeps local neighbourhoods,
so related notes cluster; without the optional `umap-learn` dependency it falls back to
PCA, which is linear and spreads clusters less clearly but needs nothing extra.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..core.store import Store

log = logging.getLogger("vaultsearch")
TEMPLATE = Path(__file__).with_name("template.html")


def _pca(x: np.ndarray) -> np.ndarray:
    x = x - x.mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    out = x @ vt[:3].T
    if out.shape[1] < 3:  # fewer notes/dims than 3
        out = np.pad(out, ((0, 0), (0, 3 - out.shape[1])))
    return out


def project(vectors: np.ndarray, method: str = "auto", seed: int = 42) -> tuple[np.ndarray, str]:
    """Reduce (n, d) vectors to (n, 3), centred and scaled to [-1, 1]. Returns (coords, method used)."""
    n = len(vectors)
    if n == 0:
        return np.zeros((0, 3), dtype=np.float32), method
    if method in ("auto", "umap"):
        try:
            import umap  # optional: `uv sync --extra viz`
        except ImportError:
            if method == "umap":
                raise SystemExit("umap-learn is not installed: run `uv sync --extra viz` or use --method pca")
            method = "pca"
        else:
            method = "umap" if n > 15 else "pca"  # UMAP needs more points than neighbours
    if method == "umap":
        reducer = umap.UMAP(n_components=3, metric="cosine", n_neighbors=15, min_dist=0.1, random_state=seed)
        coords = reducer.fit_transform(vectors)
    else:
        coords = _pca(vectors)
    coords = coords - coords.mean(axis=0)
    scale = np.abs(coords).max() or 1.0
    return (coords / scale).astype(np.float32), method


def _folder(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(root)"


def build_graph(store: Store, method: str = "auto") -> dict[str, Any]:
    """Nodes (one per note with embeddings) and undirected [[link]] edges between them."""
    files, packed = store.note_embeddings()
    rows, vecs = [], []
    for f in files:
        blobs = packed.get(f["path"])
        if not blobs:
            continue  # empty note: nothing to place
        m = np.mean([np.frombuffer(b, dtype=np.float32) for b in blobs], axis=0)
        vecs.append(m / (np.linalg.norm(m) or 1.0))
        rows.append(f)

    coords, used = project(np.array(vecs, dtype=np.float32).reshape(len(vecs), -1), method)

    # Links point at note titles ([[Kanji]]) or paths ([[Japanese/Kanji]]); resolve both case-insensitively
    index: dict[str, int] = {}
    for i, f in enumerate(rows):
        index.setdefault(f["title"].lower(), i)
        index.setdefault(Path(f["path"]).stem.lower(), i)
    edges: set[tuple[int, int]] = set()
    for i, f in enumerate(rows):
        for target in json.loads(f["links"]):
            j = index.get(target.split("|")[0].split("#")[0].rsplit("/", 1)[-1].strip().lower())
            if j is not None and j != i:
                edges.add((min(i, j), max(i, j)))

    nodes = [
        {
            "path": f["path"],
            "title": f["title"],
            "folder": _folder(f["path"]),
            "tags": json.loads(f["tags"]),
            "pos": [round(float(c), 4) for c in xyz],
        }
        for f, xyz in zip(rows, coords)
    ]
    log.info("3D map: %d notes, %d links (%s)", len(nodes), len(edges), used)
    return {"method": used, "nodes": nodes, "links": sorted(edges)}


def write_html(graph: dict[str, Any], out: Path, vault_name: str) -> Path:
    data = json.dumps({**graph, "vault": vault_name}, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    out.write_text(html, encoding="utf-8")
    return out
