"""Which graphics protocol does this terminal speak? Run it in the terminal you use vaultsearch in.

    uv run python scripts/probe_terminal.py

It prints what the terminal answered to the queries `textual-image` sends (Sixel shows up as a
4 in the primary device attributes, Kitty graphics as an OK to a test image) and what the app
will do with images. If the terminal draws images but the answer says otherwise, set
VAULTSEARCH_IMAGES=sixel or VAULTSEARCH_IMAGES=kitty.
"""
from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s", stream=sys.stderr)
for noisy in ("PIL",):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from vaultsearch.tui import images  # noqa: E402

mode = images.probe()

from textual_image import _terminal  # noqa: E402

caps = _terminal.probe_terminal()
print()
print(f"platform          {sys.platform}")
print(f"Windows Terminal  {'yes' if os.environ.get('WT_SESSION') else 'no (WT_SESSION not set)'}")
print(f"TERM_PROGRAM      {os.environ.get('TERM_PROGRAM', '-')}   TERM {os.environ.get('TERM', '-')}")
print(f"stdout is a tty   {sys.__stdout__.isatty()}")
print(f"terminal says     sixel={caps.sixel}  kitty={caps.tgp}  cell={caps.cell_size.width}x{caps.cell_size.height}px")
print(f"override          {os.environ.get(images.OVERRIDE_ENV, '-')}")
print(f"vaultsearch will  {'draw images with ' + mode if images.supported() else 'open images in the system viewer'}")
