"""Resolving [[links]] and ![[embeds]] like Obsidian (core/links.py)."""
from vaultsearch.core.links import VaultFiles


def test_links_resolve_by_name_folder_and_extension(vault):
    files = VaultFiles(vault, (".obsidian",))
    assert files.note("Kanji") == vault / "Japanese" / "Kanji.md"
    assert files.note("kanji") == vault / "Japanese" / "Kanji.md"  # case doesn't matter
    assert files.note("Japanese/Kanji") == vault / "Japanese" / "Kanji.md"
    assert files.note("Kanji.md") == vault / "Japanese" / "Kanji.md"
    assert files.note("Journal/Kanji") is None  # the folder part must match
    assert files.note("workspace") is None  # ignored folders stay out


def test_shortest_path_wins_and_new_files_are_found(vault):
    (vault / "Japanese" / "deep").mkdir()
    (vault / "Japanese" / "deep" / "Kanji.md").write_text("other", encoding="utf-8")
    files = VaultFiles(vault, stale_s=0)
    assert files.note("Kanji") == vault / "Japanese" / "Kanji.md"
    (vault / "att.png").write_bytes(b"")
    assert files.resolve("att.png") == vault / "att.png"  # missed once, so the index is rebuilt
