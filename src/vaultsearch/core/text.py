"""Small text helpers shared by the CLI and the TUI."""
from __future__ import annotations

import re

import frontmatter

from .search import WORD_RE

_WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")


def query_words(query: str) -> list[str]:
    """Words worth highlighting in results (skips very short ones)."""
    return [w for w in WORD_RE.findall(query) if len(w) > 2]


def snippet(text: str, words: list[str], width: int = 320) -> str:
    """A one-line excerpt of `text`, centred near the first matching word."""
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    low = flat.lower()
    found = [low.find(w.lower()) for w in words if low.find(w.lower()) >= 0]
    pos = min(found) if found else 0
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


def preview_markdown(raw: str) -> str:
    """Make a note renderable by a plain Markdown viewer.

    Frontmatter becomes a line of tags and [[wikilinks]] become bold text.
    """
    try:
        post = frontmatter.loads(raw)
        body, meta = post.content, post.metadata
    except Exception:
        body, meta = raw, {}
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    body = _WIKILINK_RE.sub(lambda m: f"**{m.group(2) or m.group(1)}**", body)
    head = ("`" + "`  `".join(f"#{str(t).lstrip('#')}" for t in tags) + "`\n\n") if tags else ""
    return head + body
