"""3D map of the vault: one point per note, placed by embedding similarity, joined by [[links]]."""
from .map3d import build_graph, project, write_html

__all__ = ["build_graph", "project", "write_html"]
