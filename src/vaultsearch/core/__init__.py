"""Indexing and search engine: everything that doesn't depend on a front end."""
from .config import Config
from .engine import Engine

__all__ = ["Config", "Engine", "Hit"]


def __getattr__(name: str):
    # Hit is imported on first use: search.py pulls in the store (sqlite-vec, numpy), which
    # the TUI would otherwise load before it can draw its first frame
    if name == "Hit":
        from .search import Hit

        return Hit
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
