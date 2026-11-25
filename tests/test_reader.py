"""The note reader's renderer (tui/reader.py): sections, heading lines and the cache."""
import os

from vaultsearch.core.links import VaultFiles
from vaultsearch.theme import NIGHT, PAPER
from vaultsearch.tui.reader import RenderCache, render, render_note, split_sections

NOTE = """\
Intro line.

# Kanji
Some text.

```python
# not a heading
x = 1
```

## N5
The kanji 日.
### Deeper ###
"""


def test_sections_split_at_headings_but_not_inside_code():
    sections = split_sections(NOTE)
    assert [h for h, _ in sections] == [None, "Kanji", "N5", "Deeper"]
    assert "# not a heading" in sections[1][1]


def test_heading_lines_point_at_the_rendered_headings():
    page = render(NOTE, 60, PAPER, dark=False)
    text = [strip.text for strip in page.lines]
    assert all(s.cell_length == 60 for s in page.lines)  # padded: the paper fills the width
    for line, heading in page.headings:
        assert text[line].strip().startswith(heading), (line, heading, text[line])
    assert "# not a heading" in "\n".join(text)  # code survives as code


def test_lines_are_painted_on_the_theme_paper():
    for palette, dark in ((PAPER, False), (NIGHT, True)):
        page = render("Plain text.", 30, palette, dark)
        bgs = {seg.style.bgcolor.triplet.hex for seg in page.lines[0] if seg.style and seg.style.bgcolor}
        assert bgs == {palette["washi"]}


def test_cache_renders_once_and_notices_edits(tmp_path):
    note = tmp_path / "a.md"
    note.write_text("# A\nfirst", encoding="utf-8")
    cache = RenderCache(size=2)
    first = cache.load(note, 40, "washi", PAPER, False)
    assert cache.load(note, 40, "washi", PAPER, False) is first
    assert cache.load(note, 50, "washi", PAPER, False) is not first  # other width, other page

    note.write_text("# A\nsecond, and longer", encoding="utf-8")
    st = note.stat()
    os.utime(note, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    again = cache.load(note, 40, "washi", PAPER, False)
    assert again is not first and "second" in "".join(s.text for s in again.lines)


def test_cache_keeps_only_the_most_recent(tmp_path):
    cache = RenderCache(size=2)
    notes = []
    for name in "abc":
        p = tmp_path / f"{name}.md"
        p.write_text(name, encoding="utf-8")
        notes.append(p)
        cache.load(p, 40, "washi", PAPER, False)
    keys = [cache.key(p, 40, "washi") for p in notes]
    assert keys[0] not in cache and keys[1] in cache and keys[2] in cache


def _vault_with_links(tmp_path):
    from PIL import Image

    (tmp_path / "att").mkdir()
    (tmp_path / "Kanji.md").write_text("# Kanji\n## N5\nsun", encoding="utf-8")
    Image.new("RGB", (40, 20), (200, 30, 20)).save(tmp_path / "att" / "graph.png")
    note = tmp_path / "Note.md"
    note.write_text(
        "See [[Kanji#N5|the kanji]], [[Nowhere]] and [the web](https://example.org).\n\n"
        "![[graph.png]]\n\nAfter the image.\n",
        encoding="utf-8",
    )
    return note, VaultFiles(tmp_path)


def test_links_become_click_actions_with_targets(tmp_path):
    note, files = _vault_with_links(tmp_path)
    page = render_note(note, 60, PAPER, False, files)
    by_label = {t.label: (i, t) for i, t in enumerate(page.targets)}

    i, kanji = by_label["the kanji"]
    assert (kanji.kind, kanji.path.name, kanji.heading) == ("note", "Kanji.md", "N5")
    assert by_label["Nowhere"][1].kind == "missing"
    assert by_label["https://example.org"][1].url == "https://example.org"
    assert by_label["▣ graph.png"][1].path == tmp_path / "att" / "graph.png"

    line, x, cells, target = next(link for link in page.links if link[3] == i)
    text = page.lines[line].text
    assert text[x:x + cells] == "the kanji"
    clicks = {seg.style.meta.get("@click") for seg in page.lines[line] if seg.style and seg.style.meta}
    assert f"follow({i})" in clicks
    assert not any(seg.style and seg.style.link for strip in page.lines for seg in strip)  # no raw OSC 8 links


def test_images_are_clickable_captions_not_thumbnails(tmp_path):
    note, files = _vault_with_links(tmp_path)
    page = render_note(note, 60, PAPER, False, files)
    text = [s.text for s in page.lines]
    caption = next(n for n, t in enumerate(text) if "▣ graph.png" in t)
    assert not any("▀" in t for t in text)  # cells are too coarse: no inline picture
    i = next(i for i, t in enumerate(page.targets) if t.kind == "image")
    assert any(link[0] == caption and link[3] == i for link in page.links)


def test_unreadable_note_shows_a_message_and_is_not_cached(tmp_path):
    cache = RenderCache()
    page = cache.load(tmp_path / "gone.md", 40, "washi", PAPER, False)
    assert "Could not read" in "".join(s.text for s in page.lines)
