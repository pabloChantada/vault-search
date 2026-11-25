"""Images at full resolution inside the terminal, one at a time.

Character cells hold two colours each, so even the finest block characters leave a screenshot
of a slide unreadable. Terminals that speak a graphics protocol draw real pixels: Sixel
(Windows Terminal 1.22+, WezTerm, foot...) or Kitty's graphics protocol (Kitty, Ghostty).
`textual-image` picks whichever the terminal answers to, but it has to ask before Textual
takes over the terminal, so `probe()` runs in the CLI before the app starts.

Mixing pixels with a scrolling text view is fragile, so the reader shows images as captions
and this screen shows one image at a time in place of it. Where there is no graphics protocol, the
image opens in the system viewer instead.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Static

log = logging.getLogger("vaultsearch")
_mode: str | None = None  # "sixel", "tgp" or "none"; None until probed
OVERRIDE_ENV = "VAULTSEARCH_IMAGES"  # sixel | kitty | viewer: skip the guess
_OVERRIDES = {"sixel": "sixel", "kitty": "tgp", "tgp": "tgp", "viewer": "none", "none": "none"}


def _enable_vt_output() -> None:
    """Let escape sequences written to the Windows console reach the terminal.

    Python doesn't switch on ENABLE_VIRTUAL_TERMINAL_PROCESSING; without it the console prints
    the capability query as text, no answer comes, and the probe concludes "no graphics".
    """
    import ctypes
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(sys.__stdout__.fileno())
        kernel32 = ctypes.WinDLL("kernel32")
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (OSError, AttributeError, ValueError):
        pass  # not a console (redirected output): nothing to probe anyway


def probe() -> str:
    """Ask the terminal which graphics protocol it speaks. Call before the Textual app runs.

    `VAULTSEARCH_IMAGES=sixel|kitty|viewer` overrides the answer, for terminals that draw
    images but don't say so (or say so and don't).
    """
    global _mode
    if _mode is None:
        if sys.platform == "win32" and sys.__stdout__ is not None:
            _enable_vt_output()
        try:
            from textual_image import renderable  # importing it is what queries the terminal

            _mode = {renderable.SixelImage: "sixel", renderable.TGPImage: "tgp"}.get(renderable.Image, "none")
        except Exception as e:  # missing package, odd terminal: images open in the system viewer
            log.debug("No terminal graphics: %s", e)
            _mode = "none"
        forced = os.environ.get(OVERRIDE_ENV, "").strip().lower()
        if forced in _OVERRIDES:
            _mode = _OVERRIDES[forced]
    return _mode


def supported() -> bool:
    """True if images can be shown inside the app (the terminal was probed and draws pixels)."""
    return _mode in ("sixel", "tgp")


def fit(image_px: tuple[int, int], cells: tuple[int, int], cell_px: tuple[int, int],
        max_zoom: float = 2.0) -> tuple[int, int]:
    """The (columns, rows) that show an image as large as fits in `cells`, without distorting it.

    Small images are enlarged at most `max_zoom` times: past that they only get blurrier.
    """
    (iw, ih), (cols, rows), (cw, ch) = image_px, cells, cell_px
    if not (iw and ih and cols > 0 and rows > 0):
        return 0, 0
    scale = min(cols * cw / iw, rows * ch / ih, max_zoom)
    return max(1, min(cols, round(iw * scale / cw))), max(1, min(rows, round(ih * scale / ch)))


class ImageScreen(Screen[None]):
    """One image over the note, as large as the terminal allows. ← → walk the note's images.

    A Sixel image is redrawn from scratch on every repaint (blank cells, then the pixels), so
    nothing may repaint it while it is up. Textual repaints the top screen wherever the screen
    right below it changes, even when the top one is opaque, so the app holds back its own
    status updates while this screen shows (see VaultSearchApp._refresh_status).
    """

    DEFAULT_CSS = """
    ImageScreen { background: $paper; align: center middle; }
    ImageScreen #frame { width: 100%; height: 1fr; }
    ImageScreen #caption { width: 100%; height: 1; content-align: center middle; color: $ash; }
    """

    BINDINGS = [
        Binding("left,h,k", "step(-1)", "Previous image", show=False),
        Binding("right,l,j,space", "step(1)", "Next image", show=False),
        Binding("o,enter", "open_outside", "Open in the viewer", show=False),
        Binding("escape,q,backspace", "app.pop_screen", "Back to the note", show=False),
    ]

    def __init__(self, images: list[Path], index: int = 0):
        super().__init__()
        self.images = images
        self.index = index

    def compose(self) -> ComposeResult:
        with Middle(id="frame"):
            yield Center(id="holder")
        yield Static(id="caption")

    def on_mount(self) -> None:
        self._show()

    def on_resize(self) -> None:
        self._show()

    def action_step(self, delta: int) -> None:
        if len(self.images) > 1:
            self.index = (self.index + delta) % len(self.images)
            self._show()

    def action_open_outside(self) -> None:
        from .widgets import open_with_system

        open_with_system(self.images[self.index])

    def _show(self) -> None:
        from PIL import Image as PILImage
        from textual_image.widget import SixelImage, TGPImage, get_cell_size

        Image = SixelImage if _mode == "sixel" else TGPImage  # the protocol probed (or forced)

        path = self.images[self.index]
        holder = self.query_one("#holder", Center)
        holder.remove_children()
        count = f"{self.index + 1}/{len(self.images)}  " if len(self.images) > 1 else ""
        keys = "← → other images  " if len(self.images) > 1 else ""
        self.query_one("#caption", Static).update(
            f"[$ink]{count}{escape(path.name)}[/]   [$ash]{keys}o viewer  esc back[/]")
        frame = self.query_one("#frame").size
        cell = get_cell_size()
        try:
            with PILImage.open(path) as im:
                cols, rows = fit(im.size, (frame.width, frame.height), (cell.width, cell.height))
                # Scale to the pixels it will take: encoding a 2000 px screenshot as Sixel is slow
                px = (max(1, cols * cell.width), max(1, rows * cell.height))
                picture = im.convert("RGB").resize(px, PILImage.Resampling.LANCZOS)
        except Exception as e:  # broken or unsupported file (SVG): say so, and offer the viewer
            holder.mount(Static(f"[$ash]Can't draw {escape(path.name)} here ({e}). Press o for the viewer.[/]"))
            return
        image = Image(picture)
        image.styles.width, image.styles.height = cols, rows
        holder.mount(image)
