# vaultsearch

Chat sobre tu bóveda de Obsidian sin indexar a mano. Un servidor **MCP** que:

- **Indexa solo**: vigila la carpeta (watchdog). Cada vez que guardas, creas, borras o renombras una nota, se actualiza el índice en ~2 s. Además hace un barrido de seguridad cada 10 min, por si iCloud se come algún evento.
- **Es incremental**: cada nota se parte en trozos por encabezados y párrafos, y cada trozo lleva un hash. Si editas un párrafo, solo se recalcula ese embedding. Los renombrados no recalculan nada.
- **Hace búsqueda híbrida**: BM25 (SQLite FTS5) + similitud semántica (sqlite-vec), fusionadas con *Reciprocal Rank Fusion*. La semántica encuentra cosas por significado; las keywords encuentran nombres propios y términos exactos.
- **Funciona 100 % en local**: embeddings con `fastembed` (ONNX, CPU, sin torch). El modelo por defecto es multilingüe (español, inglés, japonés…).
- **Tiene un solo fichero de índice** fuera de la bóveda (`~/.vaultsearch/…db`), para que iCloud no lo sincronice ni lo corrompa.

Herramientas que expone al LLM:

| Tool | Para qué |
|---|---|
| `search_vault(query, limit, folder?, tag?)` | Búsqueda híbrida, filtrable por carpeta o etiqueta |
| `read_note(path)` | Nota completa + backlinks |
| `list_notes(folder?, limit)` | Notas más recientes ("¿qué escribí esta semana?") |
| `index_status()` | Nº de notas y trozos indexados |

## Instalación (Windows)

Se recomienda [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`).

```powershell
cd C:\ruta\a\vault-search
uv sync

# Primera indexación desde terminal (descarga el modelo, ~220 MB, una sola vez)
uv run vaultsearch --vault "C:\Users\TU_USUARIO\iCloudDrive\iCloud~md~obsidian\SecondBrain" index
uv run vaultsearch --vault "...\SecondBrain" search "qué pensaba de la oferta de Indra"
```

Sin uv: `python -m venv .venv`, `.venv\Scripts\activate`, `pip install -e .`

## Conectarlo a Claude Desktop

Edita `%APPDATA%\Claude\claude_desktop_config.json` (o Ajustes → Developer → Edit Config):

```json
{
  "mcpServers": {
    "vaultsearch": {
      "command": "uv",
      "args": ["--directory", "C:\\ruta\\a\\vault-search", "run", "vaultsearch", "serve"],
      "env": {
        "VAULT_PATH": "C:\\Users\\TU_USUARIO\\iCloudDrive\\iCloud~md~obsidian\\SecondBrain"
      }
    }
  }
}
```

Reinicia Claude Desktop y pregúntale cosas como *"busca en mi bóveda lo que tengo sobre kanji N4"*. Mientras Claude Desktop está abierto, el servidor sigue vivo y vigilando la bóveda, así que no tienes que hacer nada más.

Sirve igual para cualquier cliente MCP (Claude Code: `claude mcp add vaultsearch -- uv --directory ... run vaultsearch serve`).

## App interactiva (`vaultsearch`)

Sin argumentos, `vaultsearch` abre una app de terminal persistente (Textual):

- **Búsqueda en vivo**: los resultados se actualizan mientras escribes (búsqueda híbrida, sin LLM).
- **Vista previa**: al moverte por los resultados ves la nota completa renderizada y salta a la sección encontrada.
- **Abrir notas**: `Enter` u `o` la abre en Obsidian (`obsidian://`), `e` en el editor por defecto, `y` copia la ruta. Hacer clic en un resultado también la abre.
- **Watcher en directo**: el panel *watcher* muestra las notas que se reindexan al guardarlas en Obsidian.
- **Comandos `/`** con autocompletado (→): `/recent`, `/folder X`, `/tag X`, `/reindex`, `/status`, `/clear`, `/help`, `/quit`.
- `↓` desde el prompt salta a los resultados; `/` o `Esc` vuelve al prompt. `F1` ayuda, `Ctrl+Q` salir.

## CLI

```
vaultsearch [--vault RUTA] [--db RUTA] [-v] {ui,index,search,status,watch,serve}
```

- `index`: actualiza el índice con barra de progreso y muestra un resumen (nuevas, actualizadas, borradas, tamaño del índice).
- `search "texto" [-n 8] [--folder X] [--tag Y]`: resultados con la ruta de la nota, la sección y un fragmento con los términos resaltados. Antes de buscar actualiza el índice (es rápido); `--no-sync` lo evita.
- `search` sin texto: **modo interactivo**. El modelo se carga una vez y puedes hacer varias búsquedas seguidas. Enter vacío o Ctrl+C para salir.
- `status`: resumen del índice y las últimas notas modificadas.
- `watch`: indexa y se queda vigilando, sin MCP.
- `serve`: servidor MCP por stdio (lo lanza el cliente).

Consejo: define `VAULT_PATH` para no tener que pasar `--vault` cada vez:

```powershell
setx VAULT_PATH "C:\Users\TU_USUARIO\iCloudDrive\iCloud~md~obsidian\SecondBrain"
# abre una terminal nueva y luego:
uv run vaultsearch index
uv run vaultsearch          # app interactiva
```

Variables de entorno: `VAULT_PATH`, `VAULTSEARCH_DB`, `VAULTSEARCH_MODEL`.

## Cómo funciona

```
 Obsidian guarda nota ──► watchdog ──► cola con debounce (2 s)
                                          │
                                          ▼
                              chunker (encabezados/párrafos)
                                          │  hash por trozo
                              ┌───────────┴───────────┐
                         ya existe                  nuevo
                     (reutiliza id)          fastembed → vector
                              └───────────┬───────────┘
                                          ▼
                 SQLite: files · chunks · chunks_fts (FTS5) · vec_chunks (sqlite-vec)

 LLM ──► search_vault ──► FTS5 bm25 ∪ KNN vectorial ──► RRF ──► máx. 2 trozos por nota
```

Estructura del código:

```
src/vaultsearch/
  config.py    rutas y opciones
  chunker.py   Markdown → trozos (respeta bloques de código, frontmatter, #tags, [[links]])
  embedder.py  fastembed (+ embedder falso para tests)
  store.py     SQLite + FTS5 + sqlite-vec
  indexer.py   indexado incremental, borrados, renombrados, barrido completo
  watcher.py   watchdog + debounce + barrido periódico
  search.py    búsqueda híbrida con RRF
  app.py       arranque en segundo plano (el MCP responde mientras carga el modelo)
  server.py    herramientas MCP
  cli.py       línea de comandos
```

## Notas y limitaciones

- **Japonés en keywords**: FTS5 (`unicode61`) no separa palabras en japonés, porque no lleva espacios. Para japonés tira de la búsqueda semántica, que sí lo entiende. Si hiciera falta, se puede añadir una segunda tabla FTS con el tokenizador `trigram`.
- **Cambiar de modelo** (`VAULTSEARCH_MODEL`) reconstruye el índice automáticamente. Alternativas en fastembed: `intfloat/multilingual-e5-large` (mejor y más pesado) o `paraphrase-multilingual-mpnet-base-v2`.
- **Tests**: `uv run --extra dev pytest`. Usan un embedder falso, así que no descargan nada.

## Ideas para seguir

- Usar los `[[links]]` para reordenar resultados: subir notas enlazadas con las mejores.
- Filtro por fechas (notas de diario).
- Reranker (cross-encoder) sobre el top 20.
- Exportar los vectores para la vista 3D (UMAP → three.js), ya calculados aquí.
