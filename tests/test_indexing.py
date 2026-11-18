def test_index_and_search(engine):
    engine.start(watch=False, background=False)
    assert engine.store.stats()["notes"] == 2  # .obsidian is ignored
    assert engine.searcher.search("recruiter offer")[0].path == "Journal/2026-11-17.md"
    assert engine.searcher.search("rain", folder="Japanese")[0].heading == "Kanji > N4"
    assert engine.searcher.search("kanji", tag="work")[0].path.startswith("Journal")
    assert engine.store.backlinks("Kanji") == ["Journal/2026-11-17.md"]


def test_editing_a_note_reuses_unchanged_chunks(engine, vault):
    engine.start(watch=False, background=False)
    note = vault / "Japanese" / "Kanji.md"
    before = engine.store.existing_chunks("Japanese/Kanji.md")
    note.write_text(note.read_text(encoding="utf-8") + "\n## N3\nThe kanji 森 means forest.\n", encoding="utf-8")
    assert engine.indexer.index_file(note) == "updated"
    after = engine.store.existing_chunks("Japanese/Kanji.md")
    assert set(before.values()) <= set(after.values())  # old chunks keep their ids
    assert len(after) == len(before) + 1
    assert engine.indexer.index_file(note) == "unchanged"


def test_sync_detects_deletes_and_renames(engine, vault):
    engine.start(watch=False, background=False)
    (vault / "Journal" / "2026-11-17.md").unlink()
    assert engine.indexer.sync_all()["deleted"] == 1

    old = vault / "Japanese" / "Kanji.md"
    new = vault / "Japanese" / "Kanji N5-N4.md"
    ids = set(engine.store.existing_chunks("Japanese/Kanji.md").values())
    old.rename(new)
    engine.indexer.move_file(old, new)
    assert set(engine.store.existing_chunks("Japanese/Kanji N5-N4.md").values()) == ids  # nothing re-embedded


def test_progress_callback(engine):
    engine.open()
    seen = []
    engine.indexer.sync_all(on_progress=lambda done, total, rel, status: seen.append((done, total, status)))
    assert seen[-1][0] == seen[-1][1] == 2
    assert {s for *_, s in seen} == {"new"}


def _count_embed_calls(engine):
    calls = []
    embed = engine.indexer.embedder.embed
    engine.indexer.embedder.embed = lambda texts: calls.append(len(texts)) or embed(texts)
    return calls


def test_sync_batches_new_chunks_across_notes(engine):
    engine.open()
    calls = _count_embed_calls(engine)
    engine.indexer.sync_all()
    assert len(calls) == 1  # both notes fit in one batch
    assert sum(calls) == engine.store.stats()["chunks"]


def test_sync_flushes_when_the_batch_is_full(engine):
    engine.cfg.index_batch_chunks = 1
    engine.open()
    calls = _count_embed_calls(engine)
    seen = []
    engine.indexer.sync_all(on_progress=lambda done, total, rel, status: seen.append(done))
    assert len(calls) == 2  # one flush per note
    assert seen == [1, 2]
    assert engine.searcher.search("rain")[0].heading == "Kanji > N4"
