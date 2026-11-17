"""Interactive terminal front end (Textual)."""
from ..core import Config


def run(cfg: Config) -> None:
    from .app import VaultSearchApp

    VaultSearchApp(cfg).run()


__all__ = ["run"]
