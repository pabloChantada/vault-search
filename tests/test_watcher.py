import time


def _wait(cond, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_watcher_picks_up_new_and_deleted_notes(engine, vault):
    engine.start(watch=True, background=True)
    assert engine.synced.wait(10)

    (vault / "Ideas.md").write_text("# Ideas\nAn MCP server for the second brain\n", encoding="utf-8")
    assert _wait(lambda: engine.store.stats()["notes"] == 3)
    assert engine.searcher.search("MCP server")[0].path == "Ideas.md"

    (vault / "Ideas.md").unlink()
    assert _wait(lambda: engine.store.stats()["notes"] == 2)
