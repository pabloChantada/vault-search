"""Drives the Textual app headlessly."""
import asyncio

from textual.widgets import ListView

from vaultsearch.core import Config
from vaultsearch.tui.app import VaultSearchApp


def test_tui_live_search_and_recent(vault, tmp_path):
    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "tui.db", embedder="hash", debounce_s=0.3)

    async def scenario():
        app = VaultSearchApp(cfg)
        async with app.run_test(size=(140, 44)) as pilot:
            for _ in range(50):
                await pilot.pause(0.1)
                if app.state == "ready":
                    break
            assert app.state == "ready"

            await pilot.press(*"rain")
            await pilot.pause(0.8)
            results = app.query_one("#results", ListView)
            assert results.children[0].path == "Japanese/Kanji.md"

            await pilot.press("ctrl+a", "backspace", *"/recent", "enter")
            await pilot.pause(0.6)
            assert [c.path for c in results.children] == sorted(
                (c.path for c in results.children), key=lambda p: -(vault / p).stat().st_mtime
            )
            await pilot.press("ctrl+q")

    asyncio.run(scenario())
