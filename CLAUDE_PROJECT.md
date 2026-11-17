# Proyecto de Claude: vaultsearch

Para crear el proyecto en claude.ai: **Proyectos → Crear proyecto**, nombre `vaultsearch`, y pega el bloque de abajo en **Instrucciones del proyecto**. En **Conocimiento del proyecto**, conecta el repo de GitHub (`pabloChantada/vault-search`) o sube los ficheros de `src/` y el `README.md`.

---

## Instrucciones del proyecto (copiar y pegar)

Estoy desarrollando **vaultsearch**, un servidor MCP en Python que hace búsqueda híbrida sobre mi bóveda de Obsidian (SecondBrain, en iCloud, en Windows) para poder chatear con ella desde Claude Desktop.

Arquitectura actual:
- `watchdog` vigila la bóveda, con debounce de 2 s y un barrido completo cada 10 min (iCloud pierde eventos).
- Chunking por encabezados y párrafos (máx. ~1200 caracteres) con hash por trozo. Al editar solo se re-embeben los trozos que cambian, y los renombrados no re-embeben nada.
- SQLite en un solo fichero fuera de la bóveda (`~/.vaultsearch/`): tablas `files` y `chunks`, FTS5 (`unicode61`, sin diacríticos) y `sqlite-vec` (coseno).
- Embeddings locales con `fastembed`, modelo `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensiones).
- Búsqueda: BM25 + KNN fusionados con RRF (k=60), máximo 2 trozos por nota y filtros por carpeta o etiqueta.
- Herramientas MCP: `search_vault`, `read_note` (con backlinks), `list_notes`, `index_status`. Compatible con mcp 1.x (FastMCP) y 2.x (MCPServer).
- Tests con pytest y un embedder falso (`VAULTSEARCH_EMBEDDER=hash`).

Cómo quiero trabajar:
- Respuestas directas y concisas, en español.
- Cambios pequeños y justificados. Si tocas el esquema de la base de datos, sube `SCHEMA_VERSION` (fuerza un reindexado).
- Ten en cuenta Windows: rutas, iCloud bloqueando ficheros al sincronizar y logs siempre a stderr (stdout es el canal MCP).
- El proyecto también es para mi portfolio (busco trabajo junior en IA/ML), así que cuida la calidad del código, los tests y el README.

Ideas pendientes: reordenar resultados usando los `[[links]]`, filtro por fechas para el diario, reranker cross-encoder, FTS `trigram` para japonés y exportar los vectores para una vista 3D (UMAP → three.js).
