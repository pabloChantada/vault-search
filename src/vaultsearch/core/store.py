"""Single-file SQLite store: metadata + FTS5 (keywords) + sqlite-vec (vectors)."""
from __future__ import annotations

import json
import sqlite3
import struct
import threading
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

SCHEMA_VERSION = "1"


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


@dataclass
class NoteRecord:
    """Everything needed to (re)write one note's rows."""

    path: str
    mtime: float
    size: int
    file_hash: str
    title: str
    tags: list[str]
    links: list[str]
    keep: dict[int, tuple[int, str]]  # chunk_id -> (ordinal, heading) for chunks we keep
    new: list[tuple[int, str, str, str, list[float]]]  # (ordinal, heading, text, hash, embedding)


class Store:
    def __init__(self, db_path: Path, dim: int, model_id: str):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.execute("PRAGMA journal_mode=WAL")
        # With WAL, NORMAL only skips the fsync on each commit: a power cut can lose the last
        # transactions but never corrupts the file, and the index can always be rebuilt.
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        # The watcher and the MCP server / TUI read and write from different threads
        self.lock = threading.RLock()
        self.dim = dim
        self._init_schema(model_id)

    # ---------- schema ----------
    def _init_schema(self, model_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
            meta = {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM meta")}
            wanted = {"schema": SCHEMA_VERSION, "model": model_id, "dim": str(self.dim)}
            if meta and any(meta.get(k) != v for k, v in wanted.items()):
                # Embedding model or schema changed: rebuild from scratch
                for t in ("vec_chunks", "chunks_fts", "chunks", "files"):
                    self.db.execute(f"DROP TABLE IF EXISTS {t}")
            self.db.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS files(
                    path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    links TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS chunks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE ON UPDATE CASCADE,
                    ordinal INTEGER NOT NULL,
                    heading TEXT NOT NULL,
                    text TEXT NOT NULL,
                    hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    title, heading, text, tokenize='unicode61 remove_diacritics 2'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                    embedding float[{self.dim}] distance_metric=cosine
                );
                """
            )
            self.db.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", list(wanted.items())
            )

    # ---------- files ----------
    def file_state(self) -> dict[str, tuple[float, int]]:
        with self.lock:
            return {r["path"]: (r["mtime"], r["size"]) for r in self.db.execute("SELECT path, mtime, size FROM files")}

    def file_hash(self, path: str) -> str | None:
        with self.lock:
            r = self.db.execute("SELECT hash FROM files WHERE path=?", (path,)).fetchone()
            return r["hash"] if r else None

    def touch_file(self, path: str, mtime: float, size: int) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE files SET mtime=?, size=? WHERE path=?", (mtime, size, path))

    def existing_chunks(self, path: str) -> dict[str, int]:
        with self.lock:
            return {r["hash"]: r["id"] for r in self.db.execute("SELECT id, hash FROM chunks WHERE path=?", (path,))}

    def write_notes(self, records: list[NoteRecord]) -> None:
        """Write several notes in a single transaction (one commit instead of one per note)."""
        with self.lock, self.db:
            for rec in records:
                self._write_note(rec)

    def _write_note(self, rec: NoteRecord) -> None:
        path, title, keep = rec.path, rec.title, rec.keep
        self.db.execute(
            """INSERT INTO files(path, mtime, size, hash, title, tags, links) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size,
               hash=excluded.hash, title=excluded.title, tags=excluded.tags, links=excluded.links""",
            (path, rec.mtime, rec.size, rec.file_hash, title, json.dumps(rec.tags), json.dumps(rec.links)),
        )
        old_ids = [r["id"] for r in self.db.execute("SELECT id FROM chunks WHERE path=?", (path,))]
        for cid in old_ids:
            if cid not in keep:
                self._delete_chunk(cid)
        for cid, (ordinal, heading) in keep.items():
            self.db.execute("UPDATE chunks SET ordinal=?, heading=? WHERE id=?", (ordinal, heading, cid))
        # Refresh the title in FTS for kept chunks (the note may have been renamed)
        for cid in keep:
            r = self.db.execute("SELECT heading, text FROM chunks WHERE id=?", (cid,)).fetchone()
            self.db.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            self.db.execute(
                "INSERT INTO chunks_fts(rowid, title, heading, text) VALUES (?,?,?,?)",
                (cid, title, r["heading"], r["text"]),
            )
        for ordinal, heading, text, chash, emb in rec.new:
            cur = self.db.execute(
                "INSERT INTO chunks(path, ordinal, heading, text, hash) VALUES (?,?,?,?,?)",
                (path, ordinal, heading, text, chash),
            )
            cid = cur.lastrowid
            self.db.execute(
                "INSERT INTO chunks_fts(rowid, title, heading, text) VALUES (?,?,?,?)",
                (cid, title, heading, text),
            )
            self.db.execute("INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)", (cid, _pack(emb)))

    def _delete_chunk(self, cid: int) -> None:
        self.db.execute("DELETE FROM chunks WHERE id=?", (cid,))
        self.db.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        self.db.execute("DELETE FROM vec_chunks WHERE rowid=?", (cid,))

    def delete_file(self, path: str) -> None:
        with self.lock, self.db:
            for r in self.db.execute("SELECT id FROM chunks WHERE path=?", (path,)).fetchall():
                self._delete_chunk(r["id"])
            self.db.execute("DELETE FROM files WHERE path=?", (path,))

    def rename_file(self, old: str, new: str) -> bool:
        """Move a note without re-embedding anything. Returns False if it was not indexed."""
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM files WHERE path=?", (old,)).fetchone():
                return False
            if self.db.execute("SELECT 1 FROM files WHERE path=?", (new,)).fetchone():
                self.delete_file(new)
            self.db.execute("UPDATE files SET path=? WHERE path=?", (new, old))  # ON UPDATE CASCADE moves the chunks too
            return True

    # ---------- search ----------
    def fts_search(self, match_expr: str, k: int) -> list[tuple[int, float]]:
        with self.lock:
            try:
                rows = self.db.execute(
                    # bm25 weights: title 3, heading 2, body 1
                    "SELECT rowid, bm25(chunks_fts, 3.0, 2.0, 1.0) AS s FROM chunks_fts "
                    "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?",
                    (match_expr, k),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            return [(r["rowid"], r["s"]) for r in rows]

    def vec_search(self, emb: list[float], k: int) -> list[tuple[int, float]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT rowid, distance FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (_pack(emb), k),
            ).fetchall()
            return [(r["rowid"], r["distance"]) for r in rows]

    def chunks_by_ids(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        with self.lock:
            q = ",".join("?" * len(ids))
            rows = self.db.execute(
                f"SELECT c.id, c.path, c.ordinal, c.heading, c.text, f.title, f.tags, f.mtime "
                f"FROM chunks c JOIN files f ON f.path = c.path WHERE c.id IN ({q})",
                ids,
            ).fetchall()
            return {r["id"]: r for r in rows}

    def list_files(self, folder: str | None, limit: int) -> list[sqlite3.Row]:
        with self.lock:
            if folder:
                prefix = folder.strip("/") + "/"
                return self.db.execute(
                    "SELECT path, title, tags, mtime FROM files WHERE path LIKE ? ESCAPE '\\' ORDER BY mtime DESC LIMIT ?",
                    (prefix.replace("%", "\\%").replace("_", "\\_") + "%", limit),
                ).fetchall()
            return self.db.execute(
                "SELECT path, title, tags, mtime FROM files ORDER BY mtime DESC LIMIT ?", (limit,)
            ).fetchall()

    def backlinks(self, title: str) -> list[str]:
        with self.lock:
            rows = self.db.execute(
                "SELECT f.path FROM files f, json_each(f.links) j WHERE j.value = ? COLLATE NOCASE", (title,)
            ).fetchall()
            return [r["path"] for r in rows]

    def note_embeddings(self) -> tuple[list[sqlite3.Row], dict[str, list[bytes]]]:
        """All notes plus their chunks' packed float32 embeddings, grouped by note path."""
        with self.lock:
            files = self.db.execute("SELECT path, title, tags, links FROM files ORDER BY path").fetchall()
            paths = {r["id"]: r["path"] for r in self.db.execute("SELECT id, path FROM chunks")}
            vecs: dict[str, list[bytes]] = {}
            for r in self.db.execute("SELECT rowid, embedding FROM vec_chunks"):
                if (p := paths.get(r["rowid"])) is not None:
                    vecs.setdefault(p, []).append(r["embedding"])
            return files, vecs

    def stats(self) -> dict:
        with self.lock:
            return {
                "notes": self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                "chunks": self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            }
