"""Colours shared by the CLI and the TUI.

The CLI prints into whatever terminal the user has, so it keeps a palette that reads on a dark
background. The TUI paints its own background, so it borrows the ink-on-paper palette of the
3D map (viz/themes/sansui.html): `PAPER` by day, `NIGHT` for dark rooms. Keep these in sync with
`tui/styles.tcss`, which only refers to them through Textual theme variables.
"""

# ---------- CLI (dark terminal) ----------
ACCENT = "#7DD3C0"  # teal: active things, highlights
PATH = "#F2B66D"  # amber: note paths
DIM = "#9AA0A6"  # secondary text
NEW = "#A5B4FC"  # new notes / summaries
DELETED = "#F9A8D4"  # removed notes / errors
BACKGROUND = "#0F1115"

# ---------- TUI (the map's sansui palette) ----------
PAPER = {
    "washi": "#ece7db",  # paper: the background
    "washi_deep": "#e2dccd",  # paper in shadow: the selected row
    "sumi": "#18181c",  # ink: text
    "ash": "#5c574c",  # diluted ink: secondary text, rules
    "ai": "#24345a",  # indigo: paths and headings
    "moss": "#33503f",  # new notes
    "vermilion": "#b83a26",  # the seal: focus, matches, the cursor
}

NIGHT = {
    "washi": "#18181c",
    "washi_deep": "#25252b",
    "sumi": "#e2dccd",
    "ash": "#8f897c",
    "ai": "#9aaed4",
    "moss": "#8fb39c",
    "vermilion": "#d9634c",
}
