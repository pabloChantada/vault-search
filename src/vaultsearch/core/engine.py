"""The Engine wires config, embedder, store, indexer, searcher and watcher together.

Every front end (CLI, TUI, MCP server) talks to the vault through one of these.
"""
from __future__ import annotations

import logging
import threading

from .config import Config
from .embedder import make_embedder
from .indexer import Indexer, ProgressFn
from .search import Searcher
from .store import Store
from .watcher import VaultWatcher

log = logging.getLogger("vaultsearch")


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ready = threading.Event()  # embedder loaded and store open
        self.synced = threading.Event()  # first full sync finished
        self.error: Exception | None = None
        self.store: Store | None = None
        self.indexer: Indexer | None = None
        self.searcher: Searcher | None = None
        self.watcher: VaultWatcher | None = None

    def open(self) -> None:
        """Load the model and open the index, without syncing or watching."""
        log.debug("Loading embedding model (%s)...", self.cfg.embed_model)
        embedder = make_embedder(
            self.cfg.embedder, self.cfg.embed_model, self.cfg.embed_batch_size, self.cfg.embed_threads
        )
        model_id = self.cfg.embed_model if self.cfg.embedder == "fastembed" else self.cfg.embedder
        self.store = Store(self.cfg.db_path, embedder.dim, model_id)
        self.indexer = Indexer(self.cfg, self.store, embedder)
        self.searcher = Searcher(self.store, embedder)
        self.ready.set()

    def start_watching(self) -> None:
        self.watcher = VaultWatcher(self.indexer, debounce_s=self.cfg.debounce_s)
        self.watcher.start()

    def start(self, watch: bool = True, background: bool = True, on_progress: ProgressFn | None = None) -> None:
        """Open, optionally start the watcher, then run a full sync."""

        def run():
            try:
                self.open()
                if watch:
                    # Start watching before the sync so changes made during it aren't lost
                    self.start_watching()
                counts = self.indexer.sync_all(on_progress=on_progress)
                log.info("Initial sync: %s", counts)
            except Exception as e:  # surfaced when a front end calls wait_ready()
                self.error = e
                log.exception("Startup failed")
                self.ready.set()
            finally:
                self.synced.set()

        if background:
            threading.Thread(target=run, name="vaultsearch-boot", daemon=True).start()
        else:
            run()

    def wait_ready(self, timeout: float = 180) -> None:
        if not self.ready.wait(timeout):
            raise RuntimeError("The index is still starting up (loading the model). Try again in a few seconds.")
        if self.error:
            raise RuntimeError(f"vaultsearch failed to start: {self.error}")

    def stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
