"""Split Markdown notes into chunks by headings and paragraphs.

Each chunk keeps its heading path ("Japanese > Kanji > N5") so results have
context and citations read naturally.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import frontmatter

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^(```|~~~)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
TAG_RE = re.compile(r"(?<![\w/])#([\w\-/áéíóúñü]+)", re.UNICODE)


@dataclass
class Chunk:
    ordinal: int
    heading: str
    text: str

    @property
    def hash(self) -> str:
        return hashlib.sha1(f"{self.heading}\n{self.text}".encode("utf-8")).hexdigest()


@dataclass
class ParsedNote:
    title: str
    tags: list[str]
    links: list[str]
    chunks: list[Chunk]


def _split_long(text: str, max_chars: int) -> list[str]:
    """Group paragraphs up to max_chars; split huge paragraphs by sentence."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            sentences = re.split(r"(?<=[.!?。！？])\s+", p)
            piece = ""
            for s in sentences:
                if len(piece) + len(s) + 1 > max_chars and piece:
                    out.append(piece)
                    piece = ""
                piece = f"{piece} {s}".strip()
            if piece:
                out.append(piece)
            continue
        if len(buf) + len(p) + 2 > max_chars and buf:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{p}".strip()
    if buf:
        out.append(buf)
    return out


def parse_note(raw: str, title: str, max_chars: int = 1200) -> ParsedNote:
    try:
        post = frontmatter.loads(raw)
        body, meta = post.content, post.metadata
    except Exception:  # broken frontmatter: index the text as-is
        body, meta = raw, {}

    tags: set[str] = set()
    fm_tags = meta.get("tags") or []
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in re.split(r"[,\s]+", fm_tags) if t.strip()]
    tags.update(str(t).lstrip("#") for t in fm_tags)

    links = sorted({m.group(1).strip() for m in WIKILINK_RE.finditer(body)})

    # Walk line by line, respecting code fences (a '#' inside one is not a heading)
    sections: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    in_fence = False

    def flush():
        heading = " > ".join(h for _, h in stack) if stack else title
        if any(line.strip() for line in current):
            sections.append((heading, current.copy()))
        current.clear()

    for line in body.splitlines():
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            flush()
            level, text = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
        else:
            current.append(line)
            if not in_fence:
                tags.update(TAG_RE.findall(line))
    flush()

    chunks: list[Chunk] = []
    for heading, lines in sections:
        for piece in _split_long("\n".join(lines), max_chars):
            chunks.append(Chunk(ordinal=len(chunks), heading=heading, text=piece))

    # Empty or frontmatter-only note: keep one chunk with the title so it can still be found
    if not chunks:
        chunks.append(Chunk(ordinal=0, heading=title, text=title))

    return ParsedNote(title=title, tags=sorted(tags), links=links, chunks=chunks)
