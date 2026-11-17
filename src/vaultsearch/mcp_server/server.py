"""MCP server (stdio) so Claude Desktop or any MCP client can search the vault."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from ..core import Config, Engine


def build_server(cfg: Config) -> FastMCP:
    engine = Engine(cfg)
    engine.start(watch=True, background=True)

    mcp = FastMCP(
        "vaultsearch",
        instructions=(
            "Tools to search the user's Obsidian vault (their second brain). "
            "Use search_vault to find information (call it several times with different phrasings "
            "if needed) and read_note to read a whole note when a snippet isn't enough. "
            "Always cite the notes you use by their path."
        ),
    )

    def _safe_path(rel: str) -> Path:
        p = (cfg.vault / rel).resolve()
        if cfg.vault not in p.parents and p != cfg.vault:
            raise ValueError("Path is outside the vault")
        return p

    @mcp.tool()
    def search_vault(query: str, limit: int = 8, folder: str | None = None, tag: str | None = None) -> str:
        """Hybrid (semantic + keyword) search over the vault.

        Args:
            query: what you're looking for, in natural language or keywords.
            limit: number of chunks to return (1-30).
            folder: optional, restrict to a folder (e.g. "Japanese" or "Journal/2026").
            tag: optional, restrict to notes with this tag (without #).
        """
        engine.wait_ready()
        hits = engine.searcher.search(query, limit=max(1, min(limit, 30)), folder=folder, tag=tag)
        out = {"results": [h.as_dict() for h in hits]}
        if not engine.synced.is_set():
            out["notice"] = "The first indexing pass hasn't finished yet; some content may be missing."
        return json.dumps(out, ensure_ascii=False)

    @mcp.tool()
    def read_note(path: str) -> str:
        """Return a note's full content (path relative to the vault, as returned by search_vault),
        plus the notes that link to it (backlinks)."""
        engine.wait_ready()
        p = _safe_path(path)
        if not p.is_file():
            return json.dumps({"error": f"Note not found: {path}"}, ensure_ascii=False)
        text = p.read_text(encoding="utf-8", errors="replace")
        return json.dumps({"path": path, "content": text, "backlinks": engine.store.backlinks(p.stem)}, ensure_ascii=False)

    @mcp.tool()
    def list_notes(folder: str | None = None, limit: int = 30) -> str:
        """List notes by last modification time, newest first.
        Handy for questions like 'what did I write this week?'."""
        engine.wait_ready()
        rows = engine.store.list_files(folder, max(1, min(limit, 200)))
        return json.dumps(
            [
                {
                    "path": r["path"],
                    "title": r["title"],
                    "tags": json.loads(r["tags"]),
                    "modified": datetime.fromtimestamp(r["mtime"]).isoformat(timespec="minutes"),
                }
                for r in rows
            ],
            ensure_ascii=False,
        )

    @mcp.tool()
    def index_status() -> str:
        """Index status: number of notes and chunks, and whether the first sync has finished."""
        engine.wait_ready()
        return json.dumps(
            {**engine.store.stats(), "initial_sync_done": engine.synced.is_set(), "vault": str(cfg.vault)},
            ensure_ascii=False,
        )

    return mcp


def serve(cfg: Config) -> None:
    build_server(cfg).run()  # stdio
