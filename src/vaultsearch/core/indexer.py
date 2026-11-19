"""Incremental indexing: only chunks whose content changed get re-embedded."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .chunker import Chunk, parse_note
from .config import Config
from .embedder import Embedder
from .store import NoteRecord, Store

log = logging.getLogger("vaultsearch")

# on_progress(done, total, relative_path, status)
ProgressFn = Callable[[int, int, str, str], None]


@dataclass
class _Pending:
    """A changed note that is parsed and diffed, waiting for its new chunks to be embedded."""

    record: NoteRecord
    to_embed: list[Chunk]
    status: str  # "new" | "updated"


class Indexer:
    def __init__(self, cfg: Config, store: Store, embedder: Embedder):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder

    # ---------- paths ----------
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

    # ---------- operations ----------
    def index_file(self, abs_path: Path) -> str:
        """Returns 'new' | 'updated' | 'unchanged' | 'skipped'."""
        prepared = self._prepare(abs_path)
        if isinstance(prepared, str):
            return prepared
        self._flush([prepared])
        return prepared.status

    def _prepare(self, abs_path: Path) -> str | _Pending:
        """Read and chunk a note and work out which chunks need embedding.

        Returns a status string when there is nothing to write, otherwise a _Pending note.
        """
        rel = self.rel(abs_path)
        try:
            st = abs_path.stat()
            raw_bytes = abs_path.read_bytes()
        except (FileNotFoundError, PermissionError, OSError) as e:
            # iCloud sometimes locks a file while syncing; the next event or rescan will pick it up
            log.debug("Could not read %s: %s", rel, e)
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
        to_embed: list[Chunk] = []
        seen: set[str] = set()
        for ch in note.chunks:
            h = ch.hash
            if h in seen:  # duplicated paragraph inside the same note
                continue
            seen.add(h)
            if h in existing:
                keep[existing[h]] = (ch.ordinal, ch.heading)
            else:
                to_embed.append(ch)

        # st_birthtime is the real creation time on Windows/macOS; on Linux st_ctime is the
        # metadata-change time, so mtime is the honest fallback there. iCloud can reset both.
        born = getattr(st, "st_birthtime", None) or (st.st_ctime if os.name == "nt" else st.st_mtime)
        created = note.created or min(born, st.st_mtime)
        record = NoteRecord(rel, st.st_mtime, st.st_size, file_hash, note.title, note.tags, note.links, keep, [],
                            created=created)
        return _Pending(record, to_embed, "updated" if prev_hash else "new")

    def _flush(self, batch: list[_Pending]) -> None:
        """Embed the new chunks of several notes in one call, then write them in one transaction."""
        # Embed "title — heading + text": helps a lot with short chunks
        texts = [f"{p.record.title} — {c.heading}\n{c.text}" for p in batch for c in p.to_embed]
        embs = iter(self.embedder.embed(texts) if texts else [])
        for p in batch:
            p.record.new = [(c.ordinal, c.heading, c.text, c.hash, next(embs)) for c in p.to_embed]
        self.store.write_notes([p.record for p in batch])
        for p in batch:
            log.info("Indexed %s (%d new chunks, %d reused)", p.record.path, len(p.record.new), len(p.record.keep))

    def remove_file(self, abs_path: Path | str) -> None:
        rel = self.rel(abs_path)
        self.store.delete_file(rel)
        log.info("Removed %s", rel)

    def move_file(self, src: Path | str, dest: Path | str) -> None:
        src_note, dest_note = self.is_note(src), self.is_note(dest)
        if src_note and dest_note:
            if self.store.rename_file(self.rel(src), self.rel(dest)):
                log.info("Renamed %s → %s", self.rel(src), self.rel(dest))
            self.index_file(Path(dest))  # in case the content or title changed too
        elif src_note:
            self.remove_file(src)
        elif dest_note:
            self.index_file(Path(dest))

    def sync_all(self, on_progress: ProgressFn | None = None) -> dict[str, int]:
        """Cheap full rescan: only opens files whose mtime or size changed.

        Changed notes are not written one by one: their new chunks are gathered until there
        are cfg.index_batch_chunks of them, then embedded in one call and written in one
        transaction. Big batches keep the ONNX runtime busy and save a commit per note.
        """
        known = self.store.file_state()
        counts = {"new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "deleted": 0}
        present: set[str] = set()
        notes = list(self.iter_notes())
        done = 0
        batch: list[_Pending] = []

        def report(rel: str, status: str) -> None:
            nonlocal done
            done += 1
            counts[status] += 1
            if on_progress:
                on_progress(done, len(notes), rel, status)

        def flush() -> None:
            if batch:
                self._flush(batch)
                for p in batch:
                    report(p.record.path, p.status)
                batch.clear()

        for p in notes:
            rel = self.rel(p)
            present.add(rel)
            try:
                st = p.stat()
            except OSError:
                report(rel, "skipped")
                continue
            prev = known.get(rel)
            if prev and abs(prev[0] - st.st_mtime) < 1e-6 and prev[1] == st.st_size:
                report(rel, "unchanged")
                continue
            prepared = self._prepare(p)
            if isinstance(prepared, str):
                report(rel, prepared)
                continue
            batch.append(prepared)
            if sum(len(b.to_embed) for b in batch) >= self.cfg.index_batch_chunks:
                flush()
        flush()

        for rel in set(known) - present:
            self.store.delete_file(rel)
            counts["deleted"] += 1
        return counts
