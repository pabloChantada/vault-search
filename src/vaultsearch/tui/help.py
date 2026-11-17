"""Slash commands and the help page shown in the preview panel."""

COMMANDS = {
    "/recent": "recently modified notes",
    "/folder": "scope search to a folder (/folder off to clear)",
    "/tag": "scope search to a tag (/tag off to clear)",
    "/reindex": "force a full rescan of the vault",
    "/status": "index stats",
    "/clear": "clear results",
    "/help": "shortcuts and commands",
    "/quit": "exit",
}

HELP_MD = """\
# vaultsearch

Start typing — results update as you go.

| Key | Action |
|---|---|
| `↓` or `Enter` in the prompt | jump to the results |
| `↑` `↓` | move through results (the preview follows) |
| `Enter` / `o` / click | open the note in **Obsidian** |
| `e` | open the note in your default editor |
| `y` | copy the note's path |
| `/` or `Esc` | back to the prompt |
| `F1` | this help |
| `Ctrl+Q` | quit |

## Commands

""" + "\n".join(f"- `{c}` — {d}" for c, d in COMMANDS.items())
