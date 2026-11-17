"""Junta config + embedder + store + indexer + watcher en un solo objeto."""
from __future__ import annotations

import logging
import threading

from .config import Config
from .embedder import make_embedder
from .indexer import Indexer
from .search import Searcher
from .store import Store
from .watcher import VaultWatcher

log = logging.getLogger("vaultsearch")


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ready = threading.Event()  # embedder cargado y store abierto
        self.synced = threading.Event()  # primer barrido completo terminado
        self.error: Exception | None = None
        self.store: Store | None = None
        self.indexer: Indexer | None = None
        self.searcher: Searcher | None = None
        self.watcher: VaultWatcher | None = None

    def open(self) -> None:
        """Carga modelo e índice sin barrer ni vigilar (uso desde la CLI)."""
        self._boot()

    def _boot(self) -> None:
        log.info("Loading embedding model (%s)...", self.cfg.embed_model)
        embedder = make_embedder(self.cfg.embedder, self.cfg.embed_model)
        model_id = self.cfg.embed_model if self.cfg.embedder == "fastembed" else self.cfg.embedder
        self.store = Store(self.cfg.db_path, embedder.dim, model_id)
        self.indexer = Indexer(self.cfg, self.store, embedder)
        self.searcher = Searcher(self.store, embedder)
        self.ready.set()

    def start(self, watch: bool = True, background: bool = True) -> None:
        def run():
            try:
                self._boot()
                if watch:
                    # Arrancamos el watcher antes del barrido para no perder cambios hechos durante él
                    self.watcher = VaultWatcher(self.indexer, debounce_s=self.cfg.debounce_s)
                    self.watcher.start()
                counts = self.indexer.sync_all()
                log.info("Initial sync: %s", counts)
            except Exception as e:  # se reporta al usar las herramientas
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
            raise RuntimeError("El índice todavía se está inicializando (cargando el modelo). Prueba en unos segundos.")
        if self.error:
            raise RuntimeError(f"vaultsearch no pudo arrancar: {self.error}")
