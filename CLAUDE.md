# vaultsearch — notes for Claude

Hybrid search over an Obsidian vault (the owner's "SecondBrain", stored in iCloud and used on Windows). Three front ends share one engine: a Textual TUI (`vaultsearch`), Rich CLI commands, and an MCP server (`vaultsearch serve`).

## Layout

- `src/vaultsearch/core/`: the engine, with no UI code. `Engine` (engine.py) wires `Config`, the embedder, `Store`, `Indexer`, `Searcher` and `VaultWatcher` together.
- `src/vaultsearch/tui/`: the Textual app. Styles live in `styles.tcss`; keep the colours in sync with `theme.py`.
- `src/vaultsearch/cli/`: `main.py` holds argparse and dispatch; `commands.py` holds the Rich output.
- `src/vaultsearch/mcp_server/`: the MCP tools. It works with mcp 1.x (`FastMCP`) and 2.x (`MCPServer`).

## How it works

- Chunks are split by heading and paragraph (~1200 chars max) and hashed, so only changed chunks get re-embedded. Renames re-embed nothing (`ON UPDATE CASCADE`).
- Storage is a single SQLite file outside the vault (`~/.vaultsearch/`), with FTS5 (`unicode61`, diacritics removed) and sqlite-vec (cosine).
- Search runs BM25 and KNN, fuses them with RRF (k=60) and keeps at most 2 chunks per note.
- The watcher debounces for 2 s and also runs a full rescan every 10 min, because iCloud drops events.
- Embeddings use fastembed `paraphrase-multilingual-MiniLM-L12-v2` (384 dims, ONNX on CPU).

## Rules

- Changing the DB schema? Bump `SCHEMA_VERSION` in `store.py`; that forces a rebuild.
- Logs always go to stderr. In `serve`, stdout is the MCP channel. In the TUI, logs are routed to the watcher panel.
- Think about Windows: paths, iCloud locking files while it syncs, and `os.startfile`.
- Tests use `embedder="hash"` (no network). Run them with `uv run pytest`.
- The code and docs are in English. The project is also a portfolio piece, so keep code, tests and README quality high.
