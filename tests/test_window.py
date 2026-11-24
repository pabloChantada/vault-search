"""The map's own window: the command line, and the fall back to a browser tab."""
from pathlib import Path

from vaultsearch.viz import map_page, window, window_profile


def test_app_window_command(tmp_path):
    page = tmp_path / "map.html"
    cmd = window.app_window_command("chrome", page, tmp_path / "profile", size=(800, 600))
    assert cmd[0] == "chrome"
    assert f"--app={page.resolve().as_uri()}" in cmd
    assert "--window-size=800,600" in cmd
    assert f"--user-data-dir={tmp_path / 'profile'}" in cmd


def test_open_window_starts_the_browser_in_app_mode(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(window, "find_chromium", lambda: "edge")
    monkeypatch.setattr(window.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert window.open_window(tmp_path / "map.html", tmp_path / "profile") == "window"
    assert started[0][0] == "edge" and started[0][1].startswith("--app=file:")
    assert (tmp_path / "profile").is_dir()


def test_open_window_falls_back_to_the_browser(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "find_chromium", lambda: None)
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    assert window.open_window(tmp_path / "map.html", tmp_path / "profile") == "browser"
    assert opened == [(tmp_path / "map.html").resolve().as_uri()]


def test_map_lives_next_to_the_index():
    db = Path("/home/me/.vaultsearch/Brain-1234abcd.db")
    assert map_page(db) == db.with_name("Brain-1234abcd-map.html")
    assert window_profile(db) == db.parent / "map-window"
