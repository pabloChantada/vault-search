"""The map in a native window: WebView2 on Windows (Edge's engine, without Edge's chrome).

pywebview has to own the main thread of its process, and the TUI's main thread belongs to
Textual, so the window runs as a child process: `python -m vaultsearch.viz.native PAGE`.
The child reloads the page whenever the file changes, so the app redraws the map in place by
rewriting the HTML instead of opening a second window.

Optional: `uv sync --extra window`. Without pywebview, viz/window.py's app-mode browser is used.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from ..theme import PAPER

POLL_S = 0.5  # how often the window checks the page for a redraw
LOCK_NAME = "window.lock"  # held by the window process while it is open


def available() -> bool:
    return importlib.util.find_spec("webview") is not None


def _lock(fh) -> bool:
    """Take a non-blocking exclusive lock on an open file. False if another process holds it."""
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def is_open(profile: Path) -> bool:
    """Whether a map window already runs on this profile (it keeps the lock file locked)."""
    path = profile / LOCK_NAME
    if not path.exists():
        return False
    try:
        with open(path, "a+") as fh:
            if not _lock(fh):
                return True
            if sys.platform == "win32":  # we got it: let go straight away
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return False
    except OSError:
        return True  # can't even open it: something holds it


def window_command(page: Path, profile: Path) -> list[str]:
    return [sys.executable, "-m", "vaultsearch.viz.native", str(page.resolve()), "--profile", str(profile)]


def launch(page: Path, profile: Path) -> subprocess.Popen:
    """Start the window process. It keeps running if the terminal app quits."""
    cmd = window_command(page, profile)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no console flashing up
    return subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags
    )


class Api:
    """What the page may call as `window.pywebview.api.*`."""

    def open_note(self, uri: str) -> None:
        # WebView2 won't follow custom protocols itself; hand obsidian:// to the OS instead
        if not uri.startswith("obsidian://"):
            return
        if sys.platform == "win32":
            os.startfile(uri)  # noqa: S606 - the URI scheme is checked above
        else:
            webbrowser.open(uri)


def _reload_on_change(window, page: Path) -> None:
    seen = page.stat().st_mtime_ns
    while True:
        time.sleep(POLL_S)
        try:
            now = page.stat().st_mtime_ns
        except OSError:
            continue  # mid-rewrite
        if now != seen:
            seen = now
            window.load_url(page.as_uri())
            _bring_forward(window)


def _bring_forward(window) -> None:
    """Asked for the map again: show it, even if it was minimised or behind the terminal."""
    try:
        window.restore()
        window.on_top = True  # Windows won't let a background process steal focus outright,
        window.on_top = False  # but raising it above everything and back is allowed
    except Exception:  # cosmetic: the reload already happened
        pass


def main(argv: list[str] | None = None) -> None:
    import webview

    ap = argparse.ArgumentParser(prog="vaultsearch.viz.native")
    ap.add_argument("page", type=Path)
    ap.add_argument("--profile", type=Path, required=True)
    args = ap.parse_args(argv)

    args.profile.mkdir(parents=True, exist_ok=True)
    lock = open(args.profile / LOCK_NAME, "a+")  # held until the process exits  # noqa: SIM115
    if not _lock(lock):
        return  # another window won the race; it will pick up the new page itself

    window = webview.create_window(
        "vaultsearch · map",
        url=args.page.as_uri(),
        js_api=Api(),
        width=1280,
        height=860,
        min_size=(640, 420),
        background_color=PAPER["washi"],  # the map's paper, painted the paper before the page loads: no white flash
        text_select=True,
    )
    threading.Thread(target=_reload_on_change, args=(window, args.page), daemon=True).start()
    # A profile of its own keeps localStorage (the map remembers it was drawn) between launches
    webview.start(private_mode=False, storage_path=str(args.profile / "webview"))


if __name__ == "__main__":
    main()
