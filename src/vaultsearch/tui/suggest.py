"""Completion for the search box: commands after a "/", words from the vault otherwise."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable

from textual.suggester import Suggester

from ..core.text import fold

_LAST_WORD = re.compile(r"\w+$")


def last_word(value: str) -> re.Match[str] | None:
    """The word being typed at the end of `value` (none right after a space)."""
    return _LAST_WORD.search(value)


def complete(value: str, word: str) -> str:
    """`value` with its last word completed to `word`, keeping what was typed as typed.

    The ghost text can only extend the input, so typing "arb" offers "arb|ol" for "árbol".
    """
    m = last_word(value)
    if m is None or not fold(word).startswith(fold(m.group())):
        return value
    return value + word[len(m.group()) :]


class SearchSuggester(Suggester):
    """Feeds the input's ghost completion (→ accepts it) and reports every word it found.

    `find_words(prefix)` returns the vault's words for a prefix, best first. `on_words(value,
    words)` gets the whole list for `value`, to show the alternatives under the input.
    """

    def __init__(
        self,
        commands: Iterable[str],
        find_words: Callable[[str], list[str]],
        on_words: Callable[[str, list[str]], None],
    ) -> None:
        # No cache: the vault's words change as notes are edited
        super().__init__(use_cache=False, case_sensitive=True)
        self._commands = list(commands)
        self._find_words = find_words
        self._on_words = on_words

    async def get_suggestion(self, value: str) -> str | None:
        if value.startswith("/"):
            self._on_words(value, [])
            typed = value.casefold()
            return next((c for c in self._commands if c.startswith(typed)), None)
        m = last_word(value)
        # A thread, so the search box never waits on SQLite
        words = await asyncio.to_thread(self._find_words, m.group()) if m else []
        self._on_words(value, words)
        return complete(value, words[0]) if words else None

