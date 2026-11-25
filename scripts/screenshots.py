"""Regenerate the README's screenshots of the TUI from a small demo vault.

    uv run python scripts/screenshots.py

It builds the vault in a temporary folder, drives the app headlessly and writes SVGs to
docs/images/. The fake "hash" embedder keeps it offline and repeatable; search still reads
well because BM25 does most of the work on a vault this small.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from textual.widgets import Input

from vaultsearch.core import Config
from vaultsearch.tui.app import VaultSearchApp

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
SIZE = (150, 42)

NOTES = {
    "Japanese/Kanji.md": """---
tags: [japanese, kanji]
---
# Kanji

## N5
日 (sun, day), 月 (moon, month), 山 (mountain), 川 (river). Learned these by writing each one ten times.

## N4
雨 (rain), 電 (electricity), 森 (forest). Compounds like 電車 and 森林 are harder to remember than the single kanji.

## Review routine
Anki in the morning, handwriting on the bus. See [[Review method]].
""",
    "Japanese/Review method.md": """---
tags: [japanese, study]
---
# Review method

Twenty new Anki cards a day, never more. Reviews first, new cards after.

On the commute: handwrite yesterday's kanji from memory, check, rewrite the misses.
Compounds go on their own deck, with an example sentence each.
""",
    "Reading/Atomic Habits.md": """---
tags: [habits, reading]
---
# Atomic Habits

Make it obvious, attractive, easy, satisfying.

Habit stacking: attach the new habit to an existing one, e.g. kanji review right after getting on the bus.
""",
    "Journal/2026-11-10.md": """# Monday

Compound words keep slipping away faster than single kanji. Maybe I need example sentences
instead of bare words. Asked about it on the [[Kanji]] page.
""",
    "Journal/2026-11-17.md": """# Monday

Long run by the river, 12 km. Started [[Atomic Habits]] again. #journal
""",
    "Projects/vaultsearch.md": """---
tags: [projects, python]
---
# vaultsearch

Hybrid search over the vault: BM25 and embeddings, fused with RRF.

## Ideas
- A reranker over the top 20
- Show images in the reader
""",
}


def build_vault(root: Path) -> Path:
    vault = root / "SecondBrain"
    now = time.time()
    for i, (rel, text) in enumerate(NOTES.items()):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (now - i * 3600, now - i * 3600))  # a stable order for /recent
    return vault


async def shoot(cfg: Config) -> None:
    app = VaultSearchApp(cfg)
    app._warm_map = lambda: None  # its log line would land in the status bar of every shot
    async with app.run_test(size=SIZE) as pilot:
        for _ in range(100):
            await pilot.pause(0.1)
            if app.state == "ready":
                break
        else:
            raise SystemExit(f"the app never got ready: {app.state}")

        async def save(name: str) -> None:
            await pilot.pause(1.0)  # let the reader render the note in its thread
            app.save_screenshot(filename=f"{name}.svg", path=str(OUT))
            await pilot.pause(0.5)  # it is taken on the next refresh: don't type before then
            print(OUT / f"{name}.svg")

        prompt = app.query_one("#prompt", Input)
        await pilot.press(*"kanji comp")  # mid-word, so the suggestions under the box show
        await save("tui-search")
        prompt.value = ""
        await pilot.press("/")
        await save("tui-commands")
        prompt.value = ""
        await pilot.press(*"/recent", "enter")
        await save("tui-recent")
        await pilot.press("ctrl+q")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows: SQLite may still hold the file
        vault = build_vault(Path(tmp))
        cfg = Config(vault=vault, db_path=Path(tmp) / "index.db", embedder="hash", debounce_s=0.3)
        asyncio.run(shoot(cfg))


if __name__ == "__main__":
    sys.exit(main())
