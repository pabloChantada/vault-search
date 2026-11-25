"""Watch the vault and re-index in the background, with debouncing.

It also runs a full rescan every `rescan_s` seconds as a safety net:
iCloud/OneDrive on Windows don't always emit reliable file events.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .indexer import Indexer

log = logging.getLogger("vaultsearch")


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: VaultWatcher):
        self.w = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            if event.event_type in ("deleted", "moved"):
                self.w.request_full_sync()  # folder deleted/moved: a rescan is simpler
            return
        src = str(event.src_path)
        if event.event_type == "moved":
            self.w.enqueue_move(src, str(event.dest_path))
        elif event.event_type in ("created", "modified", "closed") and self.w.indexer.is_note(src):
            self.w.enqueue(src, "upsert")
        elif event.event_type == "deleted" and self.w.indexer.is_note(src):
            self.w.enqueue(src, "delete")


class VaultWatcher:
    def __init__(self, indexer: Indexer, debounce_s: float = 2.0, rescan_s: float = 600.0):
        self.indexer = indexer
        self.debounce_s = debounce_s
        self.rescan_s = rescan_s
        self._pending: dict[str, tuple[str, float]] = {}
        self._moves: list[tuple[str, str]] = []
        self._full_sync = threading.Event()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._observer = Observer()

    def enqueue(self, path: str, action: str) -> None:
        with self._lock:
            self._pending[path] = (action, time.monotonic())

    def enqueue_move(self, src: str, dest: str) -> None:
        with self._lock:
            self._moves.append((src, dest))

    def request_full_sync(self) -> None:
        self._full_sync.set()

    def start(self) -> None:
        self._observer.schedule(_Handler(self), str(self.indexer.cfg.vault), recursive=True)
        self._observer.start()
        threading.Thread(target=self._loop, name="vaultsearch-worker", daemon=True).start()
        log.info("Watching %s", self.indexer.cfg.vault)

    def stop(self) -> None:
        self._stop.set()
        self._observer.stop()

    def _loop(self) -> None:
        last_rescan = time.monotonic()
        while not self._stop.is_set():
            time.sleep(0.5)
            try:
                with self._lock:
                    moves, self._moves = self._moves, []
                for src, dest in moves:
                    self.indexer.move_file(src, dest)

                now = time.monotonic()
                with self._lock:
                    ready = {p: a for p, (a, t) in self._pending.items() if now - t >= self.debounce_s}
                    for p in ready:
                        del self._pending[p]
                for p, action in ready.items():
                    path = Path(p)
                    if action == "delete" or not path.exists():
                        self.indexer.remove_file(path)
                    else:
                        self.indexer.index_file(path)

                if self._full_sync.is_set() or now - last_rescan >= self.rescan_s:
                    self._full_sync.clear()
                    last_rescan = now
                    c = self.indexer.sync_all()
                    if any(c[k] for k in ("new", "updated", "deleted")):
                        log.info("Rescan: %s", c)
            except Exception:  # never let the worker thread die
                log.exception("Watcher error")
