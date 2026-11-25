"""The map's own window: the command line, and the fall back to a browser tab."""
from pathlib import Path

import pytest

from vaultsearch.viz import map_page, native, window, window_profile


def test_app_window_command(tmp_path):
    page = tmp_path / "map.html"
    cmd = window.app_window_command("chrome", page, tmp_path / "profile", size=(800, 600))
    assert cmd[0] == "chrome"
    assert f"--app={page.resolve().as_uri()}" in cmd
    assert "--window-size=800,600" in cmd
    assert f"--user-data-dir={tmp_path / 'profile'}" in cmd


@pytest.fixture
def no_native(monkeypatch):
    monkeypatch.setattr(native, "available", lambda: False)


def test_native_window_is_used_first_and_reused_while_open(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(native, "available", lambda: True)
    monkeypatch.setattr(native, "launch", lambda page, profile: launched.append((page, profile)))
    page, profile = tmp_path / "map.html", tmp_path / "profile"
    assert window.open_window(page, profile) == "native"
    assert launched == [(page, profile)]

    monkeypatch.setattr(native, "is_open", lambda p: True)
    assert window.open_window(page, profile) == "reloaded"
    assert len(launched) == 1, "an open window redraws itself; no second window"


def test_is_open_follows_the_lock_held_by_the_window(tmp_path):
    profile = tmp_path / "profile"
    assert not native.is_open(profile)  # never opened
    profile.mkdir()
    with open(profile / native.LOCK_NAME, "a+") as held:
        assert native._lock(held)
        # Another handle stands in for another process: Windows and flock both refuse it
        assert native.is_open(profile)
    assert not native.is_open(profile)  # the window closed: a stale file means nothing


def test_window_command_runs_the_native_module(tmp_path):
    cmd = native.window_command(tmp_path / "map.html", tmp_path / "profile")
    assert cmd[1:3] == ["-m", "vaultsearch.viz.native"]
    assert cmd[-2:] == ["--profile", str(tmp_path / "profile")]


def test_the_page_can_only_open_obsidian_links(monkeypatch):
    opened = []
    monkeypatch.setattr(native.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(native.webbrowser, "open", opened.append)
    native.Api().open_note("file:///C:/Windows/System32/calc.exe")
    assert opened == []
    native.Api().open_note("obsidian://open?vault=V&file=a.md")
    assert opened == ["obsidian://open?vault=V&file=a.md"]


def test_open_window_starts_the_browser_in_app_mode(tmp_path, monkeypatch, no_native):
    started = []
    monkeypatch.setattr(window, "find_chromium", lambda: "edge")
    monkeypatch.setattr(window.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert window.open_window(tmp_path / "map.html", tmp_path / "profile") == "window"
    assert started[0][0] == "edge" and started[0][1].startswith("--app=file:")
    assert (tmp_path / "profile").is_dir()


def test_open_window_falls_back_to_the_browser(tmp_path, monkeypatch, no_native):
    opened = []
    monkeypatch.setattr(window, "find_chromium", lambda: None)
    monkeypatch.setattr(window.webbrowser, "open", opened.append)
    assert window.open_window(tmp_path / "map.html", tmp_path / "profile") == "browser"
    assert opened == [(tmp_path / "map.html").resolve().as_uri()]


def test_map_lives_next_to_the_index():
    db = Path("/home/me/.vaultsearch/Brain-1234abcd.db")
    assert map_page(db) == db.with_name("Brain-1234abcd-map.html")
    assert window_profile(db) == db.parent / "map-window"
