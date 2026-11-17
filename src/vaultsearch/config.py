"""Configuración por variables de entorno (o argumentos de la CLI)."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_IGNORE = (".obsidian", ".trash", ".git", "node_modules", ".vaultsearch")


@dataclass
class Config:
    vault: Path
    db_path: Path
    embed_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedder: str = "fastembed"  # "fastembed" | "hash" (solo para tests)
    max_chunk_chars: int = 1200
    debounce_s: float = 2.0
    ignore_dirs: tuple[str, ...] = field(default=DEFAULT_IGNORE)

    @classmethod
    def from_env(cls, vault: str | None = None, db: str | None = None) -> "Config":
        vault_str = vault or os.environ.get("VAULT_PATH")
        if not vault_str:
            raise SystemExit("Falta la ruta de la bóveda: usa --vault o la variable VAULT_PATH")
        vault_path = Path(vault_str).expanduser().resolve()
        if not vault_path.is_dir():
            raise SystemExit(f"La bóveda no existe o no es una carpeta: {vault_path}")
        db_str = db or os.environ.get("VAULTSEARCH_DB")
        # Por defecto el índice vive FUERA de la bóveda (~/.vaultsearch/): si la bóveda está en iCloud/OneDrive,
        # un SQLite sincronizado acaba corrupto. Un fichero por bóveda.
        if db_str:
            db_path = Path(db_str).expanduser().resolve()
        else:
            tag = hashlib.sha1(str(vault_path).lower().encode()).hexdigest()[:8]
            db_path = Path.home() / ".vaultsearch" / f"{vault_path.name}-{tag}.db"
        return cls(
            vault=vault_path,
            db_path=db_path,
            embed_model=os.environ.get("VAULTSEARCH_MODEL", cls.embed_model),
            embedder=os.environ.get("VAULTSEARCH_EMBEDDER", cls.embedder),
        )
