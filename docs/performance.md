# Performance

Speed is the main goal: results should show up as you type. All numbers below come from a real vault (426 notes, ~6,500 chunks) on a Windows laptop CPU. How the pieces work is in [architecture.md](architecture.md).

## Summary

| | before | now |
| --- | --: | --: |
| search, p50 / p95 | 18.1 / 61.7 ms | **6.2 / 12.2 ms** |
| TUI on screen | 1.30 s | 1.06 s |
| first results after launch | 3.95 s | **1.40 s** (keyword), 2.30 s (hybrid) |
| `vaultsearch search "…" --no-sync` | 3.10 s | 1.93 s |
| open a 3 KB note in the reader | 250–750 ms | ~70 ms (~5 ms if prefetched) |
| open the map | ~13 s | ~0 s (cached layout) |

## Search

`scripts/bench_search.py` runs a fixed set of queries against a snapshot of your index (your real index is never touched). The queries are of five shapes: keywords, questions, long questions, Japanese, and two-letter prefixes. It reports p50/p95/p99 for each phase:

```bash
uv run python scripts/bench_search.py --vault "<path>"                   # add --json for raw numbers
uv run python scripts/bench_search.py --vault "<path>" --queries my.txt  # one query per line
```

Results with 14 queries × 30 runs, 30 results each (what the TUI asks for):

| phase (p50 / p95, ms) | sqlite-vec KNN | in-memory KNN | + common words left out of BM25 |
| --------------------- | -------------: | ------------: | ------------------------------: |
| embed the query       | 4.0 / 6.5      | 3.8 / 6.5     | 3.9 / 6.2                       |
| BM25 (FTS5)           | 0.5 / 40.7     | 0.5 / 41.5    | **0.5 / 4.9**                   |
| KNN                   | 12.4 / 13.0    | **0.5 / 0.7** | 0.5 / 0.7                       |
| fetch rows + fuse     | 0.9 / 1.5      | 1.0 / 1.6     | 1.1 / 1.7                       |
| **total**             | **18.1 / 61.7**| **6.0 / 50.1**| **6.2 / 12.2**                  |

What it shows:

- **KNN went from half of the search time to almost nothing**, with the same results. On every benchmark query, the in-memory matrix returns the same chunks in the same order as sqlite-vec, apart from exact ties.
- **The slow tail was BM25 scoring words that don't matter.** "de" is in 87% of the chunks, so a long Spanish question made FTS5 score nearly the whole index for a word whose IDF is close to 0. Leaving out terms found in more than 20% of the chunks took long questions from 30–40 ms to 1–6 ms. Their top 30 stayed the same or nearly so (28–30 of 30).
- **Prefix matching is kept on purpose.** Prefix matching only the last word would have been just as fast, but it changed up to 12 of the top 30 results: the prefix is what lets "árbol" find "árboles".
- **A search costs less than a keystroke now**, so the TUI waits only 80 ms after you stop typing (it used to wait 250 ms). Embedding the query is most of what's left, and it already runs inside onnxruntime.

## Startup

Measured from launch, with the model already downloaded:

| | before | now |
| --- | --: | --: |
| imports before the first frame | 1.26 s | 1.00 s |
| model load | 2.6 s | 1.35 s |
| TUI on screen | 1.30 s | 1.06 s |
| first (keyword) results | 3.95 s | 1.40 s |
| hybrid results | 3.95 s | 2.30 s |

- **fastembed is only used to download the model.** Importing it took ~1.7 s, mostly for its image models and HTTP clients. `OnnxEmbedder` runs the cached model with onnxruntime and tokenizers instead. The vectors are identical (max difference 0.0 on 300 real chunks), so existing indexes keep working.
- **The TUI draws before it loads the engine**, and keyword search works while the model loads.
- **What's left is mostly Python and Textual itself** (~1 s before the first frame). Going lower would need a resident process.

## Indexing

`scripts/bench_index.py` builds the index from scratch into a throwaway database, then runs a warm sync where nothing changed. It reports notes/s and how the time splits between parsing, embedding, SQLite writes and file reads:

```bash
uv run python scripts/bench_index.py --vault "<path>"            # add --json for raw numbers
uv run scalene run scripts/bench_index.py --- --vault "<path>"   # Python vs native time, memory; then `scalene view`
sudo uv run py-spy record -o index.svg -- python scripts/bench_index.py --vault "<path>"   # flame graph
```

The flags `--embed-batch`, `--index-batch` and `--threads` override the config for a run.

Results for a cold build (417 notes, ~6,400 chunks, avg 438 chars, onnxruntime's default thread count):

| ONNX batch | index batch | embed (s) | chunks/s |
| ---------: | ----------: | --------: | -------: |
| 1          | 512         | 96.5      | 65.5     |
| **8**      | **512**     | **80.8**  | **78.1** |
| 8          | 128         | 81.3      | 77.6     |
| 16         | 512         | 86.8      | 72.7     |
| 32         | 512         | 111.6     | 56.5     |
| 64         | 256         | 148.0     | 43.0     |

What it shows:

- **Embedding is ~98% of the time.** File reads, parsing and SQLite writes add up to ~1.5 s. The indexer isn't I/O-bound, and moving the Python glue to another language wouldn't help: the hot loop already runs inside onnxruntime (C++).
- **ONNX batch size follows a U-curve.**
  - At batch 1 you lose vectorisation and pay a per-call overhead.
  - Above 8, padding and larger attention matrices cost more than batching saves (attention grows with the square of the sequence length).
  - Sorting the texts by length before batching keeps the padding low.
- **Index batch doesn't matter.** 128 and 512 perform the same; the setting only trades fewer SQLite transactions against memory.
- **Threads: physical cores win.** On a 12-core / 24-thread CPU at batch 8:

  | threads | time |
  | --: | --: |
  | 4 | 131 s |
  | 12 | 88 s |
  | default | ≈ 81 s |

  Going past the logical thread count (32) slows to a crawl from oversubscription. The default is left to onnxruntime: incremental edits embed only a few chunks, so capping threads buys little.
- **A warm sync** (nothing changed) takes ~0.2 s, mostly reading and hashing files.

Further speed-ups (an int8-quantised model, shorter chunks, multi-process embedding, a GPU) would each cost retrieval quality, RAM or a heavy dependency. For an index that is built once and then updated incrementally, that isn't worth it.

## Note reader and map

- **Note reader.** Textual's Markdown widget (one widget per block) was replaced with Rich-rendered lines, a thread-side cache and a ±5 neighbour prefetch.

  | note size | before | uncached | prefetched |
  | --- | --: | --: | --: |
  | 3 KB | 250–750 ms | ~70 ms (60 ms of it a deliberate debounce) | ~5 ms |
  | 40–60 KB | 0.6–4.6 s | 120–280 ms | |

- **Map layout cache.** Opening the map took ~13 s, mostly importing umap/numba. Now the layout is cached and warmed in the background by the TUI, so it opens at once. A refit takes ~0.9 s. Seeding it with the old layout and aligning it with Procrustes cut the mean drift of notes from 0.405 to 0.142 (on a [-1, 1] range).
