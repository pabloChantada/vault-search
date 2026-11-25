"""Word suggestions: what the store offers, and how the search box completes them."""
from vaultsearch.core import Config, Engine
from vaultsearch.core import store as store_mod
from vaultsearch.tui.suggest import complete, last_word


def _engine(vault, tmp_path):
    engine = Engine(Config(vault=vault.resolve(), db_path=tmp_path / "index.db", embedder="hash"))
    engine.start(watch=False, background=False)
    return engine


def test_words_are_ranked_by_how_many_chunks_use_them(vault, tmp_path):
    for i in range(5):
        (vault / f"k{i}.md").write_text(f"# K{i}\nkatakana practice, katakana again.\n", encoding="utf-8")
    store = _engine(vault, tmp_path).store
    assert store.suggest_words("ka")[:2] == ["katakana", "kanji"]  # in 5 chunks, then in 3
    assert store.suggest_words("Ka") == store.suggest_words("ka")
    assert store.suggest_words("k") == []  # one letter is too little to go on
    assert store.suggest_words("kanji") == []  # the word itself isn't a suggestion


def test_words_come_back_as_they_are_usually_written(vault, tmp_path):
    (vault / "Trees.md").write_text("# Trees\nUn árbol binario. Otro árbol. Un arbol sin tilde.\n", encoding="utf-8")
    store = _engine(vault, tmp_path).store
    assert store.suggest_words("arb") == ["árbol"]
    assert store.suggest_words("árb") == ["árbol"]


def test_common_words_and_numbers_are_not_suggested(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "COMMON_TERM_MIN_CHUNKS", 0)
    for i in range(40):
        (vault / f"n{i}.md").write_text(f"# N{i}\nthere are 2024 things here, item{i}x.\n", encoding="utf-8")
    store = _engine(vault, tmp_path).store
    assert "there" not in store.suggest_words("the")  # in every chunk
    assert store.suggest_words("20") == []


def test_suggestions_give_up_while_the_indexer_holds_the_lock(vault, tmp_path):
    store = _engine(vault, tmp_path).store
    import threading

    held, done = threading.Event(), threading.Event()

    def writer():
        with store.lock:
            held.set()
            done.wait(5)

    t = threading.Thread(target=writer)
    t.start()
    held.wait(5)
    try:
        assert store.suggest_words("ka") == []
    finally:
        done.set()
        t.join()
    assert store.suggest_words("ka") == ["kanji"]


def test_complete_extends_what_was_typed():
    assert complete("study ka", "kanji") == "study kanji"
    assert complete("arb", "árbol") == "arbol"  # the ghost text can only add to the input
    assert complete("study ", "kanji") == "study "  # nothing being typed
    assert last_word("hola mun").group() == "mun"
    assert last_word("hola ") is None
