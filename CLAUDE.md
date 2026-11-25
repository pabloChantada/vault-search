# vaultsearch — notes for Claude

Hybrid search over an Obsidian vault (the owner's "SecondBrain", stored in iCloud and used on Windows). Three front ends share one engine: a Textual TUI (`vaultsearch`), Rich CLI commands, and an MCP server (`vaultsearch serve`).

## Layout

- `src/vaultsearch/core/`: the engine, with no UI code. `Engine` (engine.py) wires `Config`, the embedder, `Store`, `Indexer`, `Searcher` and `VaultWatcher` together.
- `src/vaultsearch/tui/`: the Textual app. The note reader (`NoteView`) doesn't use Textual's Markdown widget, which was 250 ms to 4 s per note. `reader.py` renders notes with Rich to lines in a thread, records where each heading starts, and caches the result; the app prefetches the ±5 results around the selection. Links become `@click` actions on the reader (also reachable with `f` hints, and `backspace` goes back). `core/links.py` resolves them the way Obsidian does. Images are clickable captions, because cells are too coarse for thumbnails. `tui/images.py` shows them at full resolution in a modal screen through `textual-image` (Sixel on Windows Terminal, the Kitty protocol in Ghostty). `images.probe()` must run before the app starts, because it queries the terminal. Where neither protocol is available, images open in the system viewer. Its palette is the map's (washi paper, sumi ink, vermilion seal): `theme.py` holds the hex values, `tui/themes.py` turns them into the `washi` and `yoru` Textual themes, and `styles.tcss` only uses their variables (`$ink`, `$ash`, `$ai`, `$seal`, `$paper`...).
- `src/vaultsearch/viz/`: the 3D map. `native.py` shows it in a pywebview (WebView2) window that runs as a child process (optional extra `window`). Only one window exists at a time, held by a lock file, and it reloads whenever the page is rewritten. Without pywebview, `window.py` falls back to a Chromium app-mode window (Edge on Windows), then to the browser.
- `src/vaultsearch/cli/`: `main.py` holds argparse and dispatch; `commands.py` holds the Rich output.
- `src/vaultsearch/mcp_server/`: the MCP tools. It works with mcp 1.x (`FastMCP`) and 2.x (`MCPServer`).

## How it works

- Chunks are split by heading and paragraph (~1200 chars max) and hashed, so only changed chunks get re-embedded. Renames re-embed nothing (`ON UPDATE CASCADE`).
- `sync_all` batches across notes: `_prepare` parses and diffs each note, `_flush` embeds the pending chunks in one call and writes them with `Store.write_notes` in one transaction. Measure changes with `scripts/bench_index.py`.
- Storage is a single SQLite file outside the vault (`~/.vaultsearch/`), with FTS5 (`unicode61`, diacritics removed) and sqlite-vec (cosine).
- Search runs BM25 and KNN, fuses them with RRF (k=60) and keeps at most 2 chunks per note.
- The watcher debounces for 2 s and also runs a full rescan every 10 min, because iCloud drops events.
- The map's positions are cached in `{db}-layout.json`, keyed by `Store.embedding_fingerprint()`. The TUI warms it in a background thread after the first sync. A refit is seeded with the old layout and Procrustes-aligned to it, so notes keep their place.
- Embeddings use fastembed `paraphrase-multilingual-MiniLM-L12-v2` (384 dims, ONNX on CPU).

## Rules

- Changing the DB schema? Bump `SCHEMA_VERSION` in `store.py`; that forces a rebuild.
- Logs always go to stderr. In `serve`, stdout is the MCP channel. In the TUI, logs are routed to the watcher panel.
- Think about Windows: paths, iCloud locking files while it syncs, and `os.startfile`.
- Tests use `embedder="hash"` (no network). Run them with `uv run pytest`.
- Changed how the TUI looks? Regenerate the README's screenshots with `uv run python scripts/screenshots.py`.
- The code and docs are in English. The project is also a portfolio piece, so keep code, tests and README quality high.
