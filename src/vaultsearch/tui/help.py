"""Slash commands, the shortcut sheet and the text shown before the first search."""

COMMANDS = {
    "/recent": "recently edited notes",
    "/folder": "search one folder only (/folder off to clear)",
    "/tag": "search one tag only (/tag off to clear)",
    "/map": "open the 3D map",
    "/reindex": "rescan the whole vault",
    "/quit": "exit",
}

# (section, [(keys, what they do)]): shown by ? / F1
SHORTCUTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Search", [
        ("type", "search as you go"),
        ("tab  shift+tab", "next, previous suggested word"),
        ("→", "take the suggested word"),
        ("enter  ↓", "go to the results"),
        ("/  esc", "back to the search box"),
    ]),
    ("Results", [
        ("j  k", "next, previous result"),
        ("l  tab", "read the note"),
        ("enter  o", "open in Obsidian"),
        ("e", "open in your editor"),
        ("y", "copy the path"),
    ]),
    ("Note", [
        ("j  k", "scroll a line"),
        ("ctrl+d  ctrl+u", "half a page down, up"),
        ("space  b", "a page down, up"),
        ("g  G", "top, bottom"),
        ("]  [", "next, previous heading"),
        ("f", "follow a link: press the letter stamped on it"),
        ("click", "follow a link, or show an image (← → the others, o your viewer)"),
        ("backspace", "back to the note you came from"),
        ("h  esc", "back to the results"),
    ]),
    ("Anywhere", [
        ("m", "open the 3D map"),
        ("ctrl+p", "commands"),
        ("?  f1", "these shortcuts"),
        ("ctrl+q", "quit"),
    ]),
]

WELCOME_MD = """\
# Search your vault

Type a word, a phrase or a question. Results update as you type, and the
note opens here.

Press `?` for the keys, `ctrl+p` for commands.
"""
