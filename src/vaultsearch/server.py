"""Servidor MCP (stdio) para usar la bóveda desde Claude Desktop u otro cliente MCP."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .app import App
from .config import Config


def build_server(cfg: Config) -> FastMCP:
    app = App(cfg)
    app.start(watch=True, background=True)

    mcp = FastMCP(
        "vaultsearch",
        instructions=(
            "Herramientas para buscar en la bóveda de Obsidian (second brain) de Pablo. "
            "Usa search_vault para encontrar información (puedes llamarla varias veces con distintas "
            "formulaciones), y read_note para leer una nota entera cuando un fragmento no baste. "
            "Cita siempre las notas que uses por su ruta."
        ),
    )

    def _safe_path(rel: str) -> Path:
        p = (cfg.vault / rel).resolve()
        if cfg.vault not in p.parents and p != cfg.vault:
            raise ValueError("Ruta fuera de la bóveda")
        return p

    @mcp.tool()
    def search_vault(query: str, limit: int = 8, folder: str | None = None, tag: str | None = None) -> str:
        """Búsqueda híbrida (semántica + palabras clave) en la bóveda.

        Args:
            query: lo que buscas, en lenguaje natural o palabras clave.
            limit: número de fragmentos a devolver (1-30).
            folder: opcional, limita a una carpeta (p. ej. "Japonés" o "Diario/2026").
            tag: opcional, limita a notas con esa etiqueta (sin #).
        """
        app.wait_ready()
        hits = app.searcher.search(query, limit=max(1, min(limit, 30)), folder=folder, tag=tag)
        out = {"results": [h.as_dict() for h in hits]}
        if not app.synced.is_set():
            out["aviso"] = "El primer indexado aún no ha terminado; puede faltar contenido."
        return json.dumps(out, ensure_ascii=False)

    @mcp.tool()
    def read_note(path: str) -> str:
        """Devuelve el contenido completo de una nota (ruta relativa a la bóveda, como sale en search_vault),
        junto con las notas que la enlazan (backlinks)."""
        app.wait_ready()
        p = _safe_path(path)
        if not p.is_file():
            return json.dumps({"error": f"No existe la nota {path}"}, ensure_ascii=False)
        text = p.read_text(encoding="utf-8", errors="replace")
        return json.dumps(
            {"path": path, "content": text, "backlinks": app.store.backlinks(p.stem)}, ensure_ascii=False
        )

    @mcp.tool()
    def list_notes(folder: str | None = None, limit: int = 30) -> str:
        """Lista notas ordenadas por última modificación (las más recientes primero).
        Útil para preguntas tipo '¿qué escribí esta semana?'."""
        app.wait_ready()
        rows = app.store.list_files(folder, max(1, min(limit, 200)))
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
        """Estado del índice: nº de notas y trozos, y si el primer indexado terminó."""
        app.wait_ready()
        return json.dumps(
            {**app.store.stats(), "indexado_inicial_terminado": app.synced.is_set(), "vault": str(cfg.vault)},
            ensure_ascii=False,
        )

    return mcp


def serve(cfg: Config) -> None:
    build_server(cfg).run()  # stdio
