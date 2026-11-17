"""Runtime configuration, read from CLI flags or environment variables."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_IGNORE = (".obsidian", ".trash", ".git", "node_modules", ".vaultsearch")
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@dataclass
class Config:
    vault: Path
    db_path: Path
    embed_model: str = DEFAULT_MODEL
    embedder: str = "fastembed"  # "fastembed" | "hash" (tests only)
    max_chunk_chars: int = 1200
    debounce_s: float = 2.0
    ignore_dirs: tuple[str, ...] = field(default=DEFAULT_IGNORE)

    @classmethod
    def from_env(cls, vault: str | None = None, db: str | None = None) -> "Config":
        vault_str = vault or os.environ.get("VAULT_PATH")
        if not vault_str:
            raise SystemExit("No vault given: pass --vault or set VAULT_PATH")
        vault_path = Path(vault_str).expanduser().resolve()
        if not vault_path.is_dir():
            raise SystemExit(f"Vault not found or not a folder: {vault_path}")

        # The index lives OUTSIDE the vault by default (~/.vaultsearch/): if the vault sits in
        # iCloud/OneDrive, a synced SQLite file ends up corrupted. One file per vault.
        db_str = db or os.environ.get("VAULTSEARCH_DB")
        if db_str:
            db_path = Path(db_str).expanduser().resolve()
        else:
            tag = hashlib.sha1(str(vault_path).lower().encode()).hexdigest()[:8]
            db_path = Path.home() / ".vaultsearch" / f"{vault_path.name}-{tag}.db"

        return cls(
            vault=vault_path,
            db_path=db_path,
            embed_model=os.environ.get("VAULTSEARCH_MODEL", DEFAULT_MODEL),
            embedder=os.environ.get("VAULTSEARCH_EMBEDDER", "fastembed"),
        )
