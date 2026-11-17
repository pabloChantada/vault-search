from vaultsearch.core.chunker import parse_note
from vaultsearch.core.text import preview_markdown, snippet


def test_headings_code_fences_and_tags():
    note = parse_note("# A\n## B\nbody\n```\n# not a heading\n```\n#tag1", title="Note")
    assert note.chunks[0].heading == "A > B"
    assert "# not a heading" in note.chunks[0].text
    assert "tag1" in note.tags


def test_frontmatter_tags_and_wikilinks():
    note = parse_note("---\ntags: [x, y]\n---\nSee [[Other note|alias]] and [[Third#Section]].", title="N")
    assert note.tags == ["x", "y"]
    assert note.links == ["Other note", "Third"]


def test_empty_note_still_has_a_chunk():
    note = parse_note("---\ntags: [x]\n---\n", title="Only title")
    assert len(note.chunks) == 1 and note.chunks[0].text == "Only title"


def test_long_paragraphs_are_split():
    text = " ".join(f"Sentence number {i}." for i in range(200))
    note = parse_note(text, title="Long", max_chars=300)
    assert len(note.chunks) > 1
    assert all(len(c.text) <= 300 for c in note.chunks)


def test_snippet_centres_on_match():
    text = "filler " * 100 + "needle here" + " filler" * 100
    s = snippet(text, ["needle"], width=80)
    assert "needle" in s and s.startswith("…") and s.endswith("…")


def test_preview_markdown_strips_frontmatter():
    out = preview_markdown("---\ntags: [a]\n---\nSee [[Note|alias]]")
    assert out.startswith("`#a`") and "**alias**" in out and "---" not in out
