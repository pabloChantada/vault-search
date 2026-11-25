"""Shared fixtures. Tests use a fake embedder, so nothing is downloaded."""
from pathlib import Path

import pytest

from vaultsearch.core import Config, Engine


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / "Japanese").mkdir(parents=True)
    (v / "Journal").mkdir()
    (v / ".obsidian").mkdir()
    (v / ".obsidian" / "workspace.md").write_text("must not be indexed", encoding="utf-8")
    (v / "Japanese" / "Kanji.md").write_text(
        "---\ntags: [japanese, kanji]\n---\n# Kanji\n## N5\nThe kanji 日 means sun or day.\n\n"
        "## N4\nThe kanji 雨 means rain.\n```python\n# not a heading\n```\n",
        encoding="utf-8",
    )
    (v / "Journal" / "2026-11-17.md").write_text(
        "# Tuesday\nTalked to the recruiter about the offer. See [[Kanji]]. #work\n",
        encoding="utf-8",
    )
    return v


@pytest.fixture
def engine(vault: Path, tmp_path: Path) -> Engine:
    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "index.db", embedder="hash", debounce_s=0.3)
    e = Engine(cfg)
    yield e
    e.stop()


@pytest.fixture
def note_ready():
    """`await note_ready(pilot, view)`: wait until the reader shows the note last asked for.

    Rendering runs in a thread after a short debounce, and slow CI runners (Windows) need longer
    than a fixed pause, so tests wait for the page instead of sleeping."""

    async def wait(pilot, view, timeout: float = 10.0) -> None:
        for _ in range(int(timeout / 0.05)):
            await pilot.pause(0.05)
            if view.ready:
                return
        raise AssertionError("the note never finished rendering")

    return wait
