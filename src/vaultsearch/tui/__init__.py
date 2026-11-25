"""Interactive terminal front end (Textual)."""
from ..core import Config


def run(cfg: Config) -> None:
    from . import images
    from .app import VaultSearchApp

    images.probe()  # ask the terminal about Sixel / Kitty graphics while we still own it
    VaultSearchApp(cfg).run()


__all__ = ["run"]
