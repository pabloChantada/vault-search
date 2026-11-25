"""The full-resolution image preview (tui/images.py) and its fallback to the system viewer."""
import asyncio

from PIL import Image as PILImage
from textual.app import App

from vaultsearch.core.links import VaultFiles
from vaultsearch.tui import images, widgets
from vaultsearch.tui.images import ImageScreen, fit
from vaultsearch.tui.themes import THEMES
from vaultsearch.tui.widgets import NoteView


def test_fit_fills_the_screen_without_distorting():
    # 10×20 px cells, an 80×24 cell screen = 800×480 px
    assert fit((1600, 900), (80, 24), (10, 20)) == (80, 22)  # wide: limited by the width
    assert fit((500, 1000), (80, 24), (10, 20)) == (24, 24)  # tall: limited by the height
    assert fit((100, 50), (80, 24), (10, 20)) == (20, 5)  # small: enlarged at most 2×
    assert fit((0, 0), (80, 24), (10, 20)) == (0, 0)


def test_the_environment_can_override_the_probe(monkeypatch):
    for value, mode in (("sixel", "sixel"), ("Kitty", "tgp"), ("viewer", "none")):
        monkeypatch.setattr(images, "_mode", None)
        monkeypatch.setenv(images.OVERRIDE_ENV, value)
        assert images.probe() == mode
        assert images.supported() is (mode != "none")


def _note_with_images(tmp_path):
    for name, size in (("a.png", (300, 200)), ("b.png", (200, 400))):
        PILImage.new("RGB", size, "teal").save(tmp_path / name)
    note = tmp_path / "Note.md"
    note.write_text("First ![[a.png]] then ![[b.png]] and ![[a.png]] again.\n", encoding="utf-8")
    return note


class _Host(App):
    def __init__(self):
        super().__init__()
        for theme in THEMES:  # the screen paints with the app's palette ($paper, $ash...)
            self.register_theme(theme)
        self.theme = THEMES[0].name

    def compose(self):
        yield NoteView(id="note")


def test_image_opens_in_the_preview_and_walks_the_note_images(tmp_path, monkeypatch, note_ready):
    note = _note_with_images(tmp_path)
    monkeypatch.setattr(images, "_mode", "sixel")

    async def scenario():
        async with _Host().run_test(size=(100, 30)) as pilot:
            view = pilot.app.query_one(NoteView)
            view.files = VaultFiles(tmp_path)
            view.show_file(note)
            await note_ready(pilot, view)
            second = next(i for i, t in enumerate(view.page.targets) if t.kind == "image" and t.path.name == "b.png")
            view.action_follow(second)
            await pilot.pause(0.2)
            screen = pilot.app.screen
            assert isinstance(screen, ImageScreen)
            assert [p.name for p in screen.images] == ["a.png", "b.png"]  # in order, no repeats
            assert screen.index == 1 and "2/2" in str(screen.query_one("#caption").render())
            picture = screen.query_one("#holder").children[0]
            frame = screen.query_one("#frame").size
            assert 0 < picture.styles.width.value <= frame.width
            assert 0 < picture.styles.height.value <= frame.height

            await pilot.press("right")  # wraps round to the first
            assert screen.index == 0
            await pilot.press("escape")
            assert pilot.app.screen is not screen

    asyncio.run(scenario())


def test_the_app_ticking_underneath_does_not_redraw_the_image(vault, tmp_path, monkeypatch):
    """A Sixel image is re-sent (blanked, then drawn) on every repaint: that was the flicker.
    Textual repaints the top screen wherever the screen below changes, so the app must keep
    still (status line, watcher events) while an image is up."""
    import logging

    from textual_image.widget import sixel

    from vaultsearch.core import Config
    from vaultsearch.tui.app import VaultSearchApp

    picture = vault / "a.png"
    PILImage.new("RGB", (300, 200), "teal").save(picture)
    monkeypatch.setattr(images, "_mode", "sixel")
    draws = []
    real = sixel._ImageSixelImpl.render_lines
    monkeypatch.setattr(sixel._ImageSixelImpl, "render_lines", lambda self, crop: draws.append(1) or real(self, crop))
    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "i.db", embedder="hash")

    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(100, 30)) as pilot:
            for _ in range(50):
                await pilot.pause(0.1)
                if app.state == "ready":
                    break
            await app.push_screen(ImageScreen([picture]))
            await pilot.pause(0.5)
            settled = len(draws)
            assert settled > 0
            logging.getLogger("vaultsearch").info("Indexed Journal/today.md")  # a watcher event
            await pilot.pause(2.2)  # two ticks of the status line
            assert len(draws) == settled
            await pilot.press("escape")
            assert any("Indexed" in e for e in app.events)  # held back from the screen, not lost

    asyncio.run(scenario())


def test_without_terminal_graphics_images_open_in_the_system_viewer(tmp_path, monkeypatch, note_ready):
    note = _note_with_images(tmp_path)
    opened = []
    monkeypatch.setattr(images, "_mode", "none")
    monkeypatch.setattr(widgets, "open_with_system", opened.append)

    async def scenario():
        async with _Host().run_test(size=(100, 30)) as pilot:
            view = pilot.app.query_one(NoteView)
            view.files = VaultFiles(tmp_path)
            view.show_file(note)
            await note_ready(pilot, view)
            first = next(i for i, t in enumerate(view.page.targets) if t.kind == "image")
            view.action_follow(first)
            await pilot.pause(0.1)
            assert not isinstance(pilot.app.screen, ImageScreen)

    asyncio.run(scenario())
    assert [p.name for p in opened] == ["a.png"]
