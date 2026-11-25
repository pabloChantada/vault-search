"""3D map of the vault: one point per note, placed by embedding similarity, joined by [[links]]."""
from pathlib import Path

from .map3d import build_graph, project, warm_layout, write_html
from .window import open_window


def map_page(db_path: Path) -> Path:
    """Where the TUI and `map --open` keep the map: next to the index, outside the vault."""
    return db_path.with_name(f"{db_path.stem}-map.html")


def layout_cache(db_path: Path) -> Path:
    """Where note positions are cached between builds (see viz/map3d.py)."""
    return db_path.with_name(f"{db_path.stem}-layout.json")


def window_profile(db_path: Path) -> Path:
    """The browser profile of the map window (see viz/window.py)."""
    return db_path.parent / "map-window"


__all__ = [
    "build_graph", "layout_cache", "map_page", "open_window", "project", "warm_layout", "window_profile",
    "write_html",
]
