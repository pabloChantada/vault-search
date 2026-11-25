"""Drives the Textual app headlessly."""
import asyncio

import pytest

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


def test_tui_vim_keys_move_through_results_and_note(cfg, note_ready):
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
            await note_ready(pilot, note)
            # The note opens at the section the hit came from ("Long > First" or "> Second"), not at the top
            section = results.highlighted_child.heading.split(" > ")[-1]
            section_line = dict((t, line) for line, t in note.page.headings)[section]
            assert note.scroll_y == section_line > 0

            await pilot.press("j", "j")
            assert note.scroll_y == section_line + 2
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


def test_tui_renders_the_neighbours_ahead(cfg, vault):
    for i in range(6):
        (vault / f"Rain {i}.md").write_text(f"# Rain {i}\nIt rained on day {i}.\n", encoding="utf-8")

    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            await pilot.press(*"rained", "enter")
            await pilot.pause(0.8)
            results = app.query_one("#results", ResultList)
            note = app.query_one("#note", NoteView)
            width, theme = note.page.width, app.theme
            assert len(results.children) >= 4
            for item in results.children[1:4]:  # rendered before anyone moved there
                assert note.cache.key(vault / item.path, width, theme) in note.cache

            await pilot.press("j")  # a cached note shows at once, without the render pause
            assert "".join(s.text for s in note.page.lines).find(results.children[1].path[:-3]) >= 0
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_follows_links_by_key_and_click_and_goes_back(cfg, vault, note_ready):
    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            await pilot.press(*"recruiter", "enter")  # the journal entry, which links [[Kanji]]
            await pilot.pause(0.8)
            await pilot.press("l")
            note = app.query_one("#note", NoteView)
            await note_ready(pilot, note)
            journal = note._source[1]
            assert journal.name == "2026-11-17.md"

            await pilot.press("f")  # hint mode: other keys cancel instead of acting
            assert note._hints
            y = note.scroll_y
            await pilot.press("G")
            assert note._hints is None and note.scroll_y == y

            await pilot.press("f", "a")  # the only link gets the first label
            await note_ready(pilot, note)
            assert note._source[1].name == "Kanji.md"
            await pilot.press("backspace")
            await note_ready(pilot, note)
            assert note._source[1] == journal

            line, x, _, _ = note.page.links[0]  # now with the mouse
            gutter = note.gutter  # border + padding on the left
            await pilot.click("#note", offset=(gutter.left + x, line - round(note.scroll_y)))
            await note_ready(pilot, note)
            assert note._source[1].name == "Kanji.md"
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
    content = mark_words(text, ["rain"])
    assert content.plain == text  # brackets in the note are text, not markup
    marked = [content.plain[s.start:s.end] for s in content.spans]
    assert marked == ["Rain", "rain"]


def test_mark_words_inside_wikilink_brackets():
    # The words of a [[link]] used to split its brackets off into markup Textual could not parse
    content = mark_words("See [[Kanji]] again", ["kanji"])
    assert content.plain == "See [[Kanji]] again"
    assert [content.plain[s.start:s.end] for s in content.spans] == ["Kanji"]


def test_the_note_follows_the_new_results_not_the_old_ones(cfg):
    # Replacing the results used to let the old list announce its first item afterwards,
    # so the reader showed a note from the previous search
    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            await pilot.press(*"weather")
            await pilot.pause(0.8)
            results = app.query_one("#results", ResultList)
            assert results.children[0].path == "Long.md"
            await pilot.press("ctrl+a", "backspace", *"rain")
            await pilot.pause(1.0)
            first = results.children[0].path
            assert first != "Long.md"
            assert app.query_one("#note", NoteView)._source[1] == cfg.vault / first
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_suggests_words_as_you_type(cfg):
    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            prompt, words = app.query_one("#prompt"), app.query_one("#words")

            await pilot.press(*"wea")
            await pilot.pause(0.3)
            assert words.display and "weather" in str(words.render())
            await pilot.press("right")  # accepts the ghost completion
            assert prompt.value == "weather"

            prompt.value = ""  # (ctrl+a is "home" in Textual's Input, not "select all")
            await pilot.press(*"study ra")
            await pilot.pause(0.3)
            assert "rain" in str(words.render())
            app.action_use_word("rain")  # what clicking a suggestion does
            assert prompt.value == "study rain " and prompt.cursor_position == len("study rain ")
            await pilot.pause(0.1)
            assert not words.display  # nothing is being typed after the space

            await pilot.press(*"we")
            await pilot.pause(0.3)
            assert words.display
            prompt.value = ""
            await pilot.pause(0.1)
            assert not words.display
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_tab_cycles_through_the_suggested_words(cfg, vault):
    (vault / "Kana.md").write_text("# Kana\nKatakana, karaoke and a katana.\n", encoding="utf-8")

    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await _ready(app, pilot)
            prompt = app.query_one("#prompt")

            await pilot.press(*"ka")
            await pilot.pause(0.3)
            words = prompt.suggester.words
            assert len(words) >= 4 and {"kanji", "katakana", "karaoke", "katana"} <= set(words)

            def offered():
                return prompt._suggestion  # the ghost text

            assert offered() == words[0]
            await pilot.press("tab")
            assert offered() == words[1] and f"[b][@click=app.use_word('{words[1]}')]" in str(
                app.query_one("#words").content)
            await pilot.press("shift+tab", "shift+tab")  # back past the first wraps to the last
            assert offered() == words[-1]
            await pilot.press("right")
            assert prompt.value == words[-1]

            prompt.value = "kanji "  # nothing being typed: tab moves the focus as before
            await pilot.pause(0.2)
            await pilot.press("tab")
            assert app.focused is not prompt
            await pilot.press("ctrl+q")

    asyncio.run(scenario())
