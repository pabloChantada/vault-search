"""Indexación incremental: solo se recalculan embeddings de los trozos que cambian."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from .chunker import parse_note
from .config import Config
from .embedder import Embedder
from .store import Store

log = logging.getLogger("vaultsearch")


class Indexer:
    def __init__(self, cfg: Config, store: Store, embedder: Embedder):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder

    # ---------- rutas ----------
    def rel(self, abs_path: Path | str) -> str:
        return Path(abs_path).resolve().relative_to(self.cfg.vault).as_posix()

    def is_note(self, abs_path: Path | str) -> bool:
        p = Path(abs_path)
        if p.suffix.lower() != ".md":
            return False
        try:
            parts = p.resolve().relative_to(self.cfg.vault).parts
        except ValueError:
            return False
        return not any(part in self.cfg.ignore_dirs or part.startswith(".") for part in parts[:-1])

    def iter_notes(self):
        for root, dirs, files in os.walk(self.cfg.vault):
            dirs[:] = [d for d in dirs if d not in self.cfg.ignore_dirs and not d.startswith(".")]
            for f in files:
                if f.lower().endswith(".md"):
                    yield Path(root) / f

    # ---------- operaciones ----------
    def index_file(self, abs_path: Path) -> str:
        """Devuelve 'new' | 'updated' | 'unchanged' | 'skipped'."""
        rel = self.rel(abs_path)
        try:
            st = abs_path.stat()
            raw_bytes = abs_path.read_bytes()
        except (FileNotFoundError, PermissionError, OSError) as e:
            # iCloud a veces bloquea el fichero mientras sincroniza; el siguiente evento/barrido lo recogerá
            log.debug("No se pudo leer %s: %s", rel, e)
            return "skipped"

        file_hash = hashlib.sha1(raw_bytes).hexdigest()
        prev_hash = self.store.file_hash(rel)
        if prev_hash == file_hash:
            self.store.touch_file(rel, st.st_mtime, st.st_size)
            return "unchanged"

        raw = raw_bytes.decode("utf-8", errors="replace")
        note = parse_note(raw, title=abs_path.stem, max_chars=self.cfg.max_chunk_chars)
        existing = self.store.existing_chunks(rel)

        keep: dict[int, tuple[int, str]] = {}
        to_embed = []
        seen: set[str] = set()
        for ch in note.chunks:
            h = ch.hash
            if h in seen:  # párrafo duplicado dentro de la misma nota
                continue
            seen.add(h)
            if h in existing:
                keep[existing[h]] = (ch.ordinal, ch.heading)
            else:
                to_embed.append(ch)

        # Se embebe "título + encabezado + texto": mejora mucho la recuperación de trozos cortos
        embs = self.embedder.embed([f"{note.title} — {c.heading}\n{c.text}" for c in to_embed]) if to_embed else []
        new = [(c.ordinal, c.heading, c.text, c.hash, e) for c, e in zip(to_embed, embs)]

        self.store.write_note(rel, st.st_mtime, st.st_size, file_hash, note.title, note.tags, note.links, keep, new)
        log.info("Indexada %s (%d trozos nuevos, %d reutilizados)", rel, len(new), len(keep))
        return "updated" if prev_hash else "new"

    def remove_file(self, abs_path: Path | str) -> None:
        rel = self.rel(abs_path)
        self.store.delete_file(rel)
        log.info("Eliminada del índice %s", rel)

    def move_file(self, src: Path | str, dest: Path | str) -> None:
        src_note, dest_note = self.is_note(src), self.is_note(dest)
        if src_note and dest_note:
            if self.store.rename_file(self.rel(src), self.rel(dest)):
                log.info("Renombrada %s -> %s", self.rel(src), self.rel(dest))
            self.index_file(Path(dest))  # por si además cambió el contenido / título
        elif src_note:
            self.remove_file(src)
        elif dest_note:
            self.index_file(Path(dest))

    def sync_all(self) -> dict[str, int]:
        """Barrido completo barato: solo abre ficheros cuyo mtime/tamaño cambió."""
        known = self.store.file_state()
        counts = {"new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "deleted": 0}
        present: set[str] = set()
        for p in self.iter_notes():
            rel = self.rel(p)
            present.add(rel)
            try:
                st = p.stat()
            except OSError:
                continue
            prev = known.get(rel)
            if prev and abs(prev[0] - st.st_mtime) < 1e-6 and prev[1] == st.st_size:
                counts["unchanged"] += 1
                continue
            counts[self.index_file(p)] += 1
        for rel in set(known) - present:
            self.store.delete_file(rel)
            counts["deleted"] += 1
        return counts
