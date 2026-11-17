"""CLI: vaultsearch {serve,index,search,watch,status}."""
from __future__ import annotations

import argparse
import logging
import sys
import time


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="vaultsearch", description="Búsqueda sobre tu bóveda de Obsidian")
    ap.add_argument("--vault", help="Ruta de la bóveda (o variable VAULT_PATH)")
    ap.add_argument("--db", help="Ruta del índice (por defecto ~/.vaultsearch/<bóveda>-<hash>.db)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="Servidor MCP por stdio (lo arranca Claude Desktop)")
    sub.add_parser("index", help="Indexa/actualiza una vez y sale")
    sub.add_parser("watch", help="Indexa y se queda vigilando cambios (sin MCP)")
    sub.add_parser("status", help="Muestra cuántas notas hay indexadas")
    s = sub.add_parser("search", help="Busca desde la terminal")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=8)
    s.add_argument("--folder")
    s.add_argument("--tag")

    args = ap.parse_args(argv)
    # Los logs SIEMPRE a stderr: stdout es el canal MCP
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from .config import Config

    cfg = Config.from_env(args.vault, args.db)

    if args.cmd == "serve":
        from .server import serve

        serve(cfg)
        return

    from .app import App

    app = App(cfg)
    if args.cmd == "watch":
        app.start(watch=True, background=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return

    app.start(watch=False, background=False)
    app.wait_ready()

    if args.cmd == "index":
        print(app.store.stats())
    elif args.cmd == "status":
        print(app.store.stats())
    elif args.cmd == "search":
        for i, h in enumerate(app.searcher.search(args.query, args.limit, args.folder, args.tag), 1):
            snippet = h.text.replace("\n", " ")
            print(f"{i}. [{h.score:.4f}] {h.path}  ›  {h.heading}")
            print(f"   {snippet[:220]}{'…' if len(snippet) > 220 else ''}\n")


if __name__ == "__main__":
    main()
