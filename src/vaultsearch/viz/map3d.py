"""Project note embeddings to 3D and render them as a self-contained three.js page.

Each note is the mean of its chunk embeddings. UMAP (cosine) keeps local neighbourhoods,
so related notes cluster; without the optional `umap-learn` dependency it falls back to
PCA, which is linear and spreads clusters less clearly but needs nothing extra.

UMAP is the slow part (importing it alone compiles numba code), so positions are cached in a
JSON file next to the index, keyed by `Store.embedding_fingerprint()`. When the fingerprint
moves, the previous layout seeds the new fit, so notes keep their place and new ones appear
next to their neighbours instead of the whole map reshuffling.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..core.store import Store

log = logging.getLogger("vaultsearch")
THEMES_DIR = Path(__file__).with_name("themes")
DEFAULT_THEME = "sansui"
LAYOUT_VERSION = 1  # bump when the cached layout's format or meaning changes
_BUILD_LOCK = threading.Lock()


def _pca(x: np.ndarray) -> np.ndarray:
    x = x - x.mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    out = x @ vt[:3].T
    if out.shape[1] < 3:  # fewer notes/dims than 3
        out = np.pad(out, ((0, 0), (0, 3 - out.shape[1])))
    return out


def project(
    vectors: np.ndarray, method: str = "auto", seed: int = 42, dims: int = 3, init: np.ndarray | None = None
) -> tuple[np.ndarray, str]:
    """Reduce (n, d) vectors to (n, dims), centred and scaled to [-1, 1]. Returns (coords, method used).

    Fit 2D and 3D separately rather than dropping an axis: clusters that are apart only in the
    dropped dimension would otherwise land on top of each other. `init` (n, dims) seeds UMAP with
    a previous layout so the map stays recognisable between builds; PCA ignores it.
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
        reducer = umap.UMAP(
            n_components=dims, metric="cosine", n_neighbors=15, min_dist=0.1, random_state=seed,
            init=init if init is not None else "spectral",
        )
        coords = reducer.fit_transform(vectors)
    else:
        coords = _pca(vectors)[:, :dims]
    coords = coords - coords.mean(axis=0)
    scale = np.abs(coords).max() or 1.0
    return (coords / scale).astype(np.float32), method


def _folder(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(root)"


def _seed(matrix: np.ndarray, paths: list[str], previous: dict[str, list[float]], dims: int) -> np.ndarray | None:
    """Start UMAP from the last layout: known notes where they were, new ones next to their nearest
    known neighbour. None when too little of the vault is known for that to help."""
    known = [i for i, p in enumerate(paths) if len(previous.get(p, ())) == dims]
    if not known or len(known) < len(paths) / 2:
        return None
    init = np.zeros((len(paths), dims), dtype=np.float32)
    k = np.array(known)
    init[k] = [previous[paths[i]] for i in known]
    is_known = set(known)
    new = np.array([i for i in range(len(paths)) if i not in is_known], dtype=int)
    if len(new):
        nearest = k[(matrix[new] @ matrix[k].T).argmax(axis=1)]  # rows are unit vectors: dot = cosine
        init[new] = init[nearest] + np.random.default_rng(0).normal(scale=0.02, size=(len(new), dims))
    return init


def _align(coords: np.ndarray, paths: list[str], previous: dict[str, list[float]]) -> np.ndarray:
    """Rotate, scale and shift a fresh layout onto the previous one (orthogonal Procrustes).

    A refit is free to come out mirrored or turned; matching it to where the known notes were
    keeps the map recognisable. On this author's vault it more than halved how far notes moved.
    """
    dims = coords.shape[1]
    known = [i for i, p in enumerate(paths) if len(previous.get(p, ())) == dims]
    if len(known) <= dims:
        return coords  # too few points to pin a rotation down
    new = coords[known]
    old = np.array([previous[paths[i]] for i in known], dtype=np.float64)
    mn, mo = new.mean(axis=0), old.mean(axis=0)
    a, b = new - mn, old - mo
    if not (a**2).sum():
        return coords
    u, s, vt = np.linalg.svd(a.T @ b)
    out = (coords - mn) @ (u @ vt) * (s.sum() / (a**2).sum()) + mo
    return (out / max(1.0, float(np.abs(out).max()))).astype(np.float32)  # stay inside [-1, 1]


def _read_layout(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data.get("version") == LAYOUT_VERSION else None
    except (OSError, ValueError, AttributeError):
        return None  # missing, half-written or from an older format: just recompute


def _write_layout(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)  # atomic, so a crash mid-write never leaves a broken cache
    except OSError as e:
        log.warning("Could not save the map layout (%s); it will be recomputed next time", e)


def _layout(store: Store, method: str, cache: Path | None) -> tuple[list, dict[str, list], dict[str, list], str]:
    """Rows of the notes on the map plus their 3D and 2D positions, from the cache when it's fresh."""
    fingerprint = store.embedding_fingerprint() if cache else None  # read first: a later edit then misses
    previous = _read_layout(cache) if cache else None
    if previous and previous["fingerprint"] == fingerprint and previous["requested"] == method:
        rows = [f for f in store.notes() if f["path"] in previous["pos"]]
        return rows, previous["pos"], previous["pos2"], previous["method"]

    t0 = time.perf_counter()
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
    paths = [f["path"] for f in rows]
    same = previous is not None and previous["requested"] == method
    coords, used = project(matrix, method, init=_seed(matrix, paths, previous["pos"], 3) if same else None)
    # Ground plane for flat and landscape themes
    coords2, _ = project(matrix, used, dims=2, init=_seed(matrix, paths, previous["pos2"], 2) if same else None)
    if same:
        coords, coords2 = _align(coords, paths, previous["pos"]), _align(coords2, paths, previous["pos2"])
    pos = {p: [round(float(c), 4) for c in xyz] for p, xyz in zip(paths, coords)}
    pos2 = {p: [round(float(c), 4) for c in xy] for p, xy in zip(paths, coords2)}
    log.info("Map layout: %d notes placed with %s in %.1fs", len(paths), used.upper(), time.perf_counter() - t0)
    if cache:
        _write_layout(cache, {
            "version": LAYOUT_VERSION, "fingerprint": fingerprint, "requested": method, "method": used,
            "pos": pos, "pos2": pos2,
        })
    return rows, pos, pos2, used


def warm_layout(store: Store, cache: Path, method: str = "auto") -> None:
    """Make sure `cache` holds a fresh layout, so the next `build_graph` with it is instant."""
    with _BUILD_LOCK:
        _layout(store, method, cache)


def build_graph(store: Store, method: str = "auto", cache: Path | None = None) -> dict[str, Any]:
    """Nodes (one per note with embeddings) and undirected [[link]] edges between them.

    With `cache`, positions are reused while no note's embeddings have changed (see `warm_layout`).
    """
    with _BUILD_LOCK:  # a background warm-up and an open request never fit UMAP twice
        rows, pos, pos2, used = _layout(store, method, cache)

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
            "pos": pos[f["path"]],
            "pos2": pos2[f["path"]],
        }
        for f in rows
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
