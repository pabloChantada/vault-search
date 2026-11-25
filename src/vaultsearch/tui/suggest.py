"""Completion for the search box: commands after a "/", words from the vault otherwise."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable

from textual.binding import Binding
from textual.suggester import Suggester
from textual.widgets import Input

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
    words, selected)` gets the whole list for `value`, to show the alternatives under the
    input with the one the ghost text offers marked. `SearchInput` cycles through them.
    """

    def __init__(
        self,
        commands: Iterable[str],
        find_words: Callable[[str], list[str]],
        on_words: Callable[[str, list[str], int], None],
    ) -> None:
        # No cache: the vault's words change as notes are edited
        super().__init__(use_cache=False, case_sensitive=True)
        self._commands = list(commands)
        self._find_words = find_words
        self.on_words = on_words
        self.value = ""  # what `words` were found for
        self.words: list[str] = []

    async def get_suggestion(self, value: str) -> str | None:
        if value.startswith("/"):
            words: list[str] = []
            typed = value.casefold()
            suggestion = next((c for c in self._commands if c.startswith(typed)), None)
        else:
            m = last_word(value)
            # A thread, so the search box never waits on SQLite
            words = await asyncio.to_thread(self._find_words, m.group()) if m else []
            suggestion = complete(value, words[0]) if words else None
        self.value, self.words = value, words
        self.on_words(value, words, 0)
        return suggestion


class SearchInput(Input):
    """The search box: tab and shift+tab cycle through the suggested words.

    The ghost text follows the choice, so → takes whichever word is marked. With nothing
    suggested, tab moves the focus as usual.
    """

    BINDINGS = [
        Binding("tab", "cycle_word(1)", "Next word", show=False),
        Binding("shift+tab", "cycle_word(-1)", "Previous word", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._selected = 0
        self._cycling: tuple[str, list[str]] | None = None  # the value and words being cycled

    def action_cycle_word(self, step: int) -> None:
        sugg = self.suggester
        words = sugg.words if isinstance(sugg, SearchSuggester) and sugg.value == self.value else []
        if not words:
            self.screen.focus_next() if step > 0 else self.screen.focus_previous()
            return
        if self._cycling is None or self._cycling[0] != self.value or self._cycling[1] is not words:
            self._cycling, self._selected = (self.value, words), 0  # a new list: the ghost shows its first word
        self._selected = (self._selected + step) % len(words)
        self._suggestion = complete(self.value, words[self._selected])
        sugg.on_words(self.value, words, self._selected)
