"""Resolve [[wikilinks]] and ![[embeds]] to files the way Obsidian does.

Obsidian links by name, not path: `[[Kanji]]` is whichever `Kanji.md` is in the vault, and
`![[graph.png]]` whichever `graph.png` (the attachments folder is just a convention). A link
that includes a folder (`[[Japanese/Kanji]]`) is matched against the end of the path. When
several files share a name, the one with the shortest path wins, as in Obsidian.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

NOTE_EXT = ".md"
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"})


class VaultFiles:
    """An index of the vault's files by name, rebuilt lazily when a lookup misses."""

    def __init__(self, vault: Path, ignore_dirs: tuple[str, ...] = (), stale_s: float = 30.0):
        self.vault = vault
        self.ignore_dirs = set(ignore_dirs)
        self.stale_s = stale_s
        self._by_name: dict[str, list[str]] = {}  # lower-case file name -> vault-relative paths
        self._built = 0.0
        self._lock = threading.Lock()

    def _build(self) -> None:
        by_name: dict[str, list[str]] = {}
        for root, dirs, files in os.walk(self.vault):
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs and not d.startswith(".")]
            rel_root = os.path.relpath(root, self.vault).replace(os.sep, "/")
            for f in files:
                rel = f if rel_root == "." else f"{rel_root}/{f}"
                by_name.setdefault(f.lower(), []).append(rel)
        for paths in by_name.values():
            paths.sort(key=lambda p: (p.count("/"), len(p), p))
        self._by_name, self._built = by_name, time.monotonic()

    def _lookup(self, target: str) -> str | None:
        target = target.strip().replace("\\", "/").lstrip("/")
        name = target.rsplit("/", 1)[-1].lower()
        candidates = self._by_name.get(name, [])
        if "/" in target:  # a partial path: it must match the end of the file's path
            suffix = "/" + target.lower()
            candidates = [p for p in candidates if ("/" + p.lower()).endswith(suffix)]
        return candidates[0] if candidates else None

    def resolve(self, target: str, default_ext: str = "") -> Path | None:
        """The file a link points at, or None. `default_ext` is tried when the link has none
        (`[[Kanji]]` means `Kanji.md`)."""
        names = [target]
        if default_ext and Path(target).suffix.lower() != default_ext:
            names.insert(0, target + default_ext)
        with self._lock:
            if not self._built:
                self._build()
            for attempt in range(2):
                for name in names:
                    if (rel := self._lookup(name)) is not None:
                        return self.vault / rel
                if attempt == 0 and time.monotonic() - self._built > self.stale_s:
                    self._build()  # maybe the file is new: look again once, then give up
                else:
                    break
        return None

    def note(self, target: str) -> Path | None:
        return self.resolve(target, NOTE_EXT)
