"""Textual themes built from the map's palette (see vaultsearch/theme.py)."""
from __future__ import annotations

from textual.theme import Theme

from ..theme import NIGHT, PAPER


def _theme(name: str, p: dict[str, str], dark: bool) -> Theme:
    return Theme(
        name=name,
        dark=dark,
        primary=p["vermilion"],
        secondary=p["ai"],
        accent=p["vermilion"],
        warning=p["vermilion"],
        error=p["vermilion"],
        success=p["moss"],
        foreground=p["sumi"],
        background=p["washi"],
        surface=p["washi"],
        panel=p["washi_deep"],
        boost=p["washi_deep"],
        variables={
            # The names styles.tcss and the markup in widgets.py use
            "ink": p["sumi"],
            "ash": p["ash"],
            "ai": p["ai"],
            "moss": p["moss"],
            "seal": p["vermilion"],
            "paper": p["washi"],
            "paper-deep": p["washi_deep"],
            # Textual's own widgets
            "block-cursor-background": p["washi_deep"],
            "block-cursor-foreground": p["sumi"],
            "block-cursor-text-style": "none",
            "block-cursor-blurred-background": p["washi_deep"],
            "block-cursor-blurred-text-style": "none",
            "block-hover-background": p["washi_deep"],
            "input-cursor-background": p["vermilion"],
            "input-cursor-foreground": p["washi"],
            "input-selection-background": f"{p['vermilion']} 25%",
            "border": p["vermilion"],
            "border-blurred": p["ash"],
            "scrollbar": p["washi_deep"],
            "scrollbar-hover": p["ash"],
            "scrollbar-active": p["vermilion"],
            "scrollbar-background": p["washi"],
            "scrollbar-background-hover": p["washi"],
            "scrollbar-background-active": p["washi"],
            "scrollbar-corner-color": p["washi"],
            "footer-background": p["washi"],
            "footer-key-foreground": p["vermilion"],
            "footer-description-foreground": p["ash"],
            "link-color": p["ai"],
            "link-color-hover": p["vermilion"],
            "link-background-hover": p["washi_deep"],
            "screen-selection-background": f"{p['vermilion']} 25%",
        },
    )


PAPER_THEME = _theme("washi", PAPER, dark=False)
NIGHT_THEME = _theme("yoru", NIGHT, dark=True)
THEMES = (PAPER_THEME, NIGHT_THEME)
