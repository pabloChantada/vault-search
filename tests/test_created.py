from datetime import datetime

from vaultsearch.core.chunker import creation_time, parse_note


def ts(*args):
    return datetime(*args).timestamp()


def test_created_from_frontmatter_date_and_string():
    assert parse_note("---\ncreated: 2024-03-05\n---\nbody", "x").created == ts(2024, 3, 5)
    assert creation_time({"Created": "2025-01-02T10:30:00"}, "x") == ts(2025, 1, 2, 10, 30)


def test_created_from_daily_note_title():
    assert parse_note("body", "2026-11-17").created == ts(2026, 11, 17)


def test_created_unknown_without_hints():
    assert parse_note("body", "Kanji").created is None
    assert creation_time({"created": "someday"}, "Kanji") is None


def test_index_stores_a_creation_time(engine):
    engine.open()
    engine.indexer.sync_all()
    rows = {r["path"]: r["created"] for r in engine.store.db.execute("SELECT path, created FROM files")}
    assert rows["Journal/2026-11-17.md"] == ts(2026, 11, 17)  # from the title
    assert rows["Japanese/Kanji.md"] > 0  # from the filesystem
