"""Open the map in a window of its own instead of a browser tab.

Chromium browsers have an app mode (`--app=URL`): a bare window with no tabs, address bar or
toolbar, which is as close to a native pop-up as a web page gets without bundling a webview.
Edge ships with Windows, so on the platform this project targets the window is always there;
elsewhere we look for Chrome, Chromium, Brave or Edge and fall back to the default browser.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

log = logging.getLogger("vaultsearch")

_WINDOWS_CANDIDATES = (
    r"Microsoft\Edge\Application\msedge.exe",
    r"Google\Chrome\Application\chrome.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
)
_MAC_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
_PATH_CANDIDATES = ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser", "chrome")


def find_chromium() -> str | None:
    """Path to a Chromium-based browser that understands --app, or None."""
    if sys.platform == "win32":
        roots = [os.environ.get(v) for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        for root in filter(None, roots):
            for rel in _WINDOWS_CANDIDATES:
                exe = Path(root) / rel
                if exe.is_file():
                    return str(exe)
    elif sys.platform == "darwin":
        for exe in _MAC_CANDIDATES:
            if Path(exe).is_file():
                return exe
    for name in _PATH_CANDIDATES:
        if found := shutil.which(name):
            return found
    return None


def app_window_command(browser: str, page: Path, profile: Path, size: tuple[int, int] = (1280, 860)) -> list[str]:
    """The command line that opens `page` as a bare window.

    A profile of its own keeps the window separate from the user's browser session (otherwise
    Chromium hands the URL to the running browser and may open a tab instead) and lets the map
    remember its own settings between launches.
    """
    return [
        browser,
        f"--app={page.resolve().as_uri()}",
        f"--window-size={size[0]},{size[1]}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def open_window(page: Path, profile: Path) -> str:
    """Show `page` in a window of its own, the best way available. Returns how it was shown:

    "reloaded" (the native map window was already open and redraws in place), "native"
    (a new pywebview window, see viz/native.py), "window" (a Chromium app-mode window) or
    "browser" (a plain tab).
    """
    from . import native

    if native.available():
        if native.is_open(profile):
            return "reloaded"  # it watches the page and redraws as soon as the file changes
        try:
            profile.mkdir(parents=True, exist_ok=True)
            native.launch(page, profile)
            return "native"
        except OSError as e:
            log.warning("Could not start the map window (%s); trying a browser window", e)

    browser = find_chromium()
    if browser:
        profile.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(
                app_window_command(browser, page, profile),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # closing the terminal must not close the map
            )
            return "window"
        except OSError as e:
            log.warning("Could not start %s (%s); opening the map in the browser instead", browser, e)
    webbrowser.open(page.resolve().as_uri())
    return "browser"
