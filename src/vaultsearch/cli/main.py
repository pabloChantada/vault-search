"""Entry point: `vaultsearch [ui|index|search|status|watch|map|serve]`."""
from __future__ import annotations

import argparse
import logging
import sys

from rich.logging import RichHandler


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vaultsearch", description="Hybrid search over your Obsidian vault")
    ap.add_argument("--vault", help="Vault path (or the VAULT_PATH env var)")
    ap.add_argument("--db", help="Index path (default ~/.vaultsearch/<vault>-<hash>.db)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("ui", help="Interactive search app (default when no command is given)")
    sub.add_parser("index", help="Index/update the vault with a progress bar")
    s = sub.add_parser("search", help="Search; with no query opens a simple prompt")
    s.add_argument("query", nargs="?", help="Query (omit for a prompt loop)")
    s.add_argument("-n", "--limit", type=int, default=8)
    s.add_argument("--folder", help="Restrict to a folder")
    s.add_argument("--tag", help="Restrict to a tag")
    s.add_argument("--no-sync", action="store_true", help="Don't update the index before searching")
    sub.add_parser("status", help="Index summary and recently modified notes")
    sub.add_parser("watch", help="Index and keep watching for changes (no MCP)")
    mp = sub.add_parser("map", help="Export a 3D map of the vault (UMAP/PCA → three.js HTML)")
    mp.add_argument("-o", "--out", default="vault-map.html", help="Output HTML file (default vault-map.html)")
    mp.add_argument("--method", choices=["auto", "umap", "pca"], default="auto",
                    help="auto = UMAP if umap-learn is installed, else PCA")
    mp.add_argument("--no-sync", action="store_true", help="Don't update the index first")
    mp.add_argument("--theme", choices=["sansui", "yggdrasil"], default="sansui",
                    help="sansui = 3D ink landscape; yggdrasil = a tree that grows with time")
    mp.add_argument("--open", action="store_true", help="Open the map in the browser")
    sub.add_parser("serve", help="MCP server over stdio (launched by Claude Desktop)")
    return ap


def _setup_logging(cmd: str, verbose: bool) -> None:
    if cmd == "serve":
        # stdout is the MCP channel: plain logs to stderr
        logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, stream=sys.stderr,
                            format="%(asctime)s %(levelname)s %(message)s")
    elif cmd != "ui":  # the TUI routes logs into its own panel
        from .commands import err

        level = logging.INFO if verbose or cmd == "watch" else logging.WARNING
        logging.basicConfig(level=level, format="%(message)s", datefmt="%H:%M:%S",
                            handlers=[RichHandler(console=err, show_path=False, markup=False)])


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "ui"
    _setup_logging(cmd, args.verbose)

    from ..core import Config

    cfg = Config.from_env(args.vault, args.db)

    if cmd == "ui":
        from ..tui import run

        run(cfg)
    elif cmd == "serve":
        from ..mcp_server import serve

        serve(cfg)
    else:
        from . import commands

        if cmd == "index":
            commands.cmd_index(cfg)
        elif cmd == "status":
            commands.cmd_status(cfg)
        elif cmd == "search":
            commands.cmd_search(cfg, args.query, args.limit, args.folder, args.tag, sync=not args.no_sync)
        elif cmd == "watch":
            commands.cmd_watch(cfg)
        elif cmd == "map":
            commands.cmd_map(cfg, args.out, args.method, args.theme, sync=not args.no_sync, open_browser=args.open)


if __name__ == "__main__":
    main()
