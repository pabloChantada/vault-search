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
THEMES_DIR = Path(__file__).with_name("themes")
DEFAULT_THEME = "sansui"


def _pca(x: np.ndarray) -> np.ndarray:
    x = x - x.mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    out = x @ vt[:3].T
    if out.shape[1] < 3:  # fewer notes/dims than 3
        out = np.pad(out, ((0, 0), (0, 3 - out.shape[1])))
    return out


def project(vectors: np.ndarray, method: str = "auto", seed: int = 42, dims: int = 3) -> tuple[np.ndarray, str]:
    """Reduce (n, d) vectors to (n, dims), centred and scaled to [-1, 1]. Returns (coords, method used).

    Fit 2D and 3D separately rather than dropping an axis: clusters that are apart only in the
    dropped dimension would otherwise land on top of each other.
    """
    n = len(vectors)
    if n == 0:
        return np.zeros((0, dims), dtype=np.float32), method
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
        reducer = umap.UMAP(n_components=dims, metric="cosine", n_neighbors=15, min_dist=0.1, random_state=seed)
        coords = reducer.fit_transform(vectors)
    else:
        coords = _pca(vectors)[:, :dims]
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

    matrix = np.array(vecs, dtype=np.float32).reshape(len(vecs), -1)
    coords, used = project(matrix, method)
    coords2, _ = project(matrix, used, dims=2)  # ground plane for flat and landscape themes

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
            "mtime": f["mtime"],  # last edit: how fresh the ink is
            "created": f["created"],  # when it was written: height in the tree
            "pos": [round(float(c), 4) for c in xyz],
            "pos2": [round(float(c), 4) for c in xy],
        }
        for f, xyz, xy in zip(rows, coords, coords2)
    ]
    log.info("3D map: %d notes, %d links (%s)", len(nodes), len(edges), used)
    return {"method": used, "nodes": nodes, "links": sorted(edges)}


def available_themes() -> list[str]:
    """Each theme is one self-contained HTML page in viz/themes/."""
    return sorted(p.stem for p in THEMES_DIR.glob("*.html"))


def write_html(graph: dict[str, Any], out: Path, vault_name: str, theme: str = DEFAULT_THEME) -> Path:
    template = THEMES_DIR / f"{theme}.html"
    if not template.is_file():
        raise ValueError(f"Unknown theme {theme!r}; available: {', '.join(available_themes())}")
    data = json.dumps({**graph, "vault": vault_name}, ensure_ascii=False).replace("</", "<\\/")
    html = template.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    out.write_text(html, encoding="utf-8")
    return out
