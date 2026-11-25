"""How a query becomes an FTS5 expression, and which terms count as common."""
from vaultsearch.core import Config, Engine
from vaultsearch.core import store as store_mod
from vaultsearch.core.search import _fts_query


def test_every_word_is_a_quoted_prefix():
    assert _fts_query('grafos "dirigidos" AND') == '"grafos"* OR "dirigidos"* OR "AND"*'
    assert _fts_query("a b") == ""  # one-letter words are skipped


def test_common_words_are_dropped_unless_nothing_else_is_left():
    common = frozenset({"de", "la", "como"})
    assert _fts_query("temas de la asignatura", common) == '"temas"* OR "asignatura"*'
    # Matching folds case and accents, as the tokenizer does: "Cómo" is the term "como"
    assert _fts_query("Cómo funciona", common) == '"funciona"*'
    assert _fts_query("de la", common) == '"de"* OR "la"*'


def test_repeated_words_are_sent_once():
    assert _fts_query("de grafos de Grafos", frozenset()) == '"de"* OR "Grafos"*'


def test_common_terms_come_from_the_index(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "COMMON_TERM_MIN_CHUNKS", 0)
    for i in range(40):  # enough notes that the fixture's kanji chunks are well under 20%
        (vault / f"note{i}.md").write_text(f"# Note {i}\nThe topic of note {i} is item{i}x.\n", encoding="utf-8")
    engine = Engine(Config(vault=vault.resolve(), db_path=tmp_path / "index.db", embedder="hash"))
    engine.start(watch=False, background=False)

    common = engine.store.common_terms()
    assert {"the", "note", "topic"} <= common
    assert "item3x" not in common and "kanji" not in common
    # Dropping them doesn't stop a search with rare words from finding its note
    assert engine.searcher.search("the topic of item3x", limit=1)[0].path == "note3.md"


def test_small_indexes_have_no_common_terms(engine):
    engine.start(watch=False, background=False)
    assert engine.store.common_terms() == frozenset()
