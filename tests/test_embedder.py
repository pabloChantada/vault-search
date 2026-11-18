from vaultsearch.core.embedder import FastEmbedder


class _FakeModel:
    """Stands in for fastembed: the 'vector' of a text is just its length."""

    def __init__(self):
        self.seen: list[str] = []

    def embed(self, texts, batch_size):
        self.seen = list(texts)
        for t in texts:
            yield _Vec([float(len(t))])


class _Vec(list):
    def tolist(self):
        return list(self)


def test_fast_embedder_sorts_by_length_and_restores_order():
    emb = FastEmbedder.__new__(FastEmbedder)  # skip loading the real model
    emb._model, emb.batch_size, emb.dim = _FakeModel(), 2, 1
    texts = ["ccc", "a", "bb", "dddd"]
    assert emb.embed(texts) == [[3.0], [1.0], [2.0], [4.0]]
    assert emb._model.seen == ["a", "bb", "ccc", "dddd"]
