"""3D map of the vault: one point per note, placed by embedding similarity, joined by [[links]]."""
from pathlib import Path

from .map3d import DEFAULT_THEME, available_themes, build_graph, project, write_html
from .window import open_window


def map_page(db_path: Path) -> Path:
    """Where the TUI and `map --open` keep the map: next to the index, outside the vault."""
    return db_path.with_name(f"{db_path.stem}-map.html")


def window_profile(db_path: Path) -> Path:
    """The browser profile of the map window (see viz/window.py)."""
    return db_path.parent / "map-window"


__all__ = [
    "DEFAULT_THEME", "available_themes", "build_graph", "map_page", "open_window", "project",
    "window_profile", "write_html",
]
