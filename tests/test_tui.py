"""Drives the Textual app headlessly."""
import asyncio

import pytest
from textual.content import Content

from vaultsearch.core import Config
from vaultsearch.tui.app import VaultSearchApp
from vaultsearch.tui.screens import ShortcutsScreen
from vaultsearch.tui.widgets import NoteView, ResultList, mark_words


async def _ready(app, pilot):
    for _ in range(50):
        await pilot.pause(0.1)
        if app.state == "ready":
            return
    raise AssertionError(f"app never got ready: {app.state}")


@pytest.fixture
def cfg(vault, tmp_path):
    long = "\n\n".join(f"Paragraph {i} about the weather." for i in range(60))
    (vault / "Long.md").write_text(f"# Long\n## First\n{long}\n## Second\n{long}\n## Third\nThe end.\n",
                                   encoding="utf-8")
    return Config(vault=vault.resolve(), db_path=tmp_path / "tui.db", embedder="hash", debounce_s=0.3)


def test_tui_live_search_and_recent(cfg, vault):
    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(140, 44)) as pilot:
            await _ready(app, pilot)

            await pilot.press(*"rain")
            await pilot.pause(0.8)
            results = app.query_one("#results", ResultList)
            assert results.children[0].path == "Japanese/Kanji.md"

            await pilot.press("ctrl+a", "backspace", *"/recent", "enter")
            await pilot.pause(0.6)
            assert [c.path for c in results.children] == sorted(
                (c.path for c in results.children), key=lambda p: -(vault / p).stat().st_mtime
            )
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_vim_keys_move_through_results_and_note(cfg):
    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            await pilot.press(*"weather", "enter")
            await pilot.pause(0.8)
            results = app.query_one("#results", ResultList)
            assert app.focused is results and results.highlighted_child.path == "Long.md"

            await pilot.press("l")
            note = app.query_one("#note", NoteView)
            assert app.focused is note
            await pilot.pause(0.3)

            await pilot.press("j", "j")
            assert note.scroll_y == 2
            await pilot.press("ctrl+d")
            assert note.scroll_y > 2
            await pilot.press("G")
            assert note.scroll_y == note.max_scroll_y > 0
            await pilot.press("g")
            assert note.scroll_y == 0
            await pilot.press("right_square_bracket")  # ] next heading: "First" sits a few lines down
            first = note.scroll_y
            await pilot.press("right_square_bracket")
            assert 0 < first < note.scroll_y
            await pilot.press("left_square_bracket")
            assert note.scroll_y == first

            await pilot.press("h")
            assert app.focused is results
            await pilot.press("escape")
            assert app.focused.id == "prompt"
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_letters_type_into_the_search_box(cfg):
    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            await pilot.press(*"mo?y")  # map, open, shortcuts, copy: all plain letters here
            assert app.query_one("#prompt").value == "mo?y"
            assert not isinstance(app.screen, ShortcutsScreen)
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_shortcuts_and_short_palette(cfg):
    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            await pilot.press("f1")
            assert isinstance(app.screen, ShortcutsScreen)
            await pilot.press("escape")
            assert not isinstance(app.screen, ShortcutsScreen)

            titles = [c.title for c in app.get_system_commands(app.screen)]
            assert "Open the 3D map" in titles and "Night colours" in titles
            assert len(titles) <= 10 and "Keys" not in titles  # only this app's own commands

            app.action_toggle_theme()
            assert app.theme == "yoru"
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_mark_words_keeps_the_text_and_marks_every_match():
    text = "see [[Kanji]] and [b]this[/b] for Rain or rain"
    content = Content.from_markup(mark_words(text, ["rain"]))
    assert content.plain == text  # brackets in the note are text, not markup
    marked = [content.plain[s.start:s.end] for s in content.spans]
    assert marked == ["Rain", "rain"]
