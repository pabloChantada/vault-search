"""Tests con un embedder falso (sin descargar modelos)."""
import time
from pathlib import Path

import pytest

from vaultsearch.app import App
from vaultsearch.chunker import parse_note
from vaultsearch.config import Config


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / "Japonés").mkdir(parents=True)
    (v / "Diario").mkdir()
    (v / ".obsidian").mkdir()
    (v / ".obsidian" / "workspace.md").write_text("no indexar", encoding="utf-8")
    (v / "Japonés" / "Kanji.md").write_text(
        "---\ntags: [japones, kanji]\n---\n# Kanji\n## N5\nEl kanji 日 significa sol o día.\n\n"
        "## N4\nEl kanji 雨 significa lluvia.\n```python\n# esto no es un encabezado\n```\n",
        encoding="utf-8",
    )
    (v / "Diario" / "2026-11-17.md").write_text(
        "# Martes\nHoy hablé con el recruiter de Indra sobre la oferta. Ver [[Kanji]]. #trabajo\n",
        encoding="utf-8",
    )
    return v


def make_app(vault: Path, tmp_path: Path) -> App:
    cfg = Config(vault=vault.resolve(), db_path=tmp_path / "idx.db", embedder="hash", debounce_s=0.3)
    app = App(cfg)
    return app


def test_chunker_headings_and_code():
    n = parse_note("# A\n## B\ntexto\n```\n# no\n```\n#tag1", title="Nota")
    assert n.chunks[0].heading == "A > B"
    assert "# no" in n.chunks[0].text
    assert "tag1" in n.tags


def test_index_and_search(vault, tmp_path):
    app = make_app(vault, tmp_path)
    app.start(watch=False, background=False)
    assert app.store.stats()["notas"] == 2  # .obsidian ignorado
    hits = app.searcher.search("recruiter Indra oferta")
    assert hits[0].path == "Diario/2026-11-17.md"
    assert app.searcher.search("lluvia", folder="Japonés")[0].heading == "Kanji > N4"
    assert app.searcher.search("kanji", tag="trabajo")[0].path.startswith("Diario")
    assert app.store.backlinks("Kanji") == ["Diario/2026-11-17.md"]


def test_incremental_reuses_chunks(vault, tmp_path):
    app = make_app(vault, tmp_path)
    app.start(watch=False, background=False)
    note = vault / "Japonés" / "Kanji.md"
    before = app.store.existing_chunks("Japonés/Kanji.md")
    note.write_text(note.read_text(encoding="utf-8") + "\n## N3\nEl kanji 森 significa bosque.\n", encoding="utf-8")
    assert app.indexer.index_file(note) == "updated"
    after = app.store.existing_chunks("Japonés/Kanji.md")
    assert set(before.values()) <= set(after.values())  # los trozos viejos se conservan (mismo id)
    assert len(after) == len(before) + 1
    assert app.indexer.index_file(note) == "unchanged"


def test_sync_detects_deletes_and_renames(vault, tmp_path):
    app = make_app(vault, tmp_path)
    app.start(watch=False, background=False)
    (vault / "Diario" / "2026-11-17.md").unlink()
    assert app.indexer.sync_all()["deleted"] == 1
    old = vault / "Japonés" / "Kanji.md"
    new = vault / "Japonés" / "Kanji N5-N4.md"
    ids = set(app.store.existing_chunks("Japonés/Kanji.md").values())
    old.rename(new)
    app.indexer.move_file(old, new)
    assert set(app.store.existing_chunks("Japonés/Kanji N5-N4.md").values()) == ids  # sin re-embeber


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_watcher_picks_up_changes(vault, tmp_path):
    app = make_app(vault, tmp_path)
    app.start(watch=True, background=True)
    assert app.synced.wait(10)
    (vault / "Ideas.md").write_text("# Ideas\nMontar un servidor MCP para el second brain\n", encoding="utf-8")
    assert _wait(lambda: app.store.stats()["notas"] == 3)
    assert app.searcher.search("servidor MCP")[0].path == "Ideas.md"
    (vault / "Ideas.md").unlink()
    assert _wait(lambda: app.store.stats()["notas"] == 2)
    app.watcher.stop()
