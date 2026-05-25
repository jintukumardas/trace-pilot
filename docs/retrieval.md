# Retrieval

`tracepilot_retrieval` owns everything between a repository's working tree and the
ranked `Evidence`/`Citation` the agent grounds on: ingestion, chunking, embeddings,
the Qdrant store, hybrid retrieval, optional rerank, and citation assembly.

Public surface (see [`INTERNAL_CONTRACTS.md`](INTERNAL_CONTRACTS.md)):

```python
from tracepilot_retrieval import (
    get_embedder, get_qdrant_store, Retriever, Ingestor, build_citations, pack_context,
)
```

---

## Ingestion pipeline

`Ingestor.ingest(repo, request, progress=...)` runs the full pipeline and returns
`RepositoryStats`. It **never raises** to the caller — a bad file is skipped with a
warning, a missing parser falls back to line windows, and a flaky vector store
degrades to a partial index.

```
open repo (GitPython)         resolve local_path, or shallow-clone git_url into WORKSPACES_DIR/<repo_id>
        │                     capture HEAD commit (repo.head.commit.hexsha)
        ▼
walk files                    os.walk, prune excluded dirs in place, apply exclusion rules
        ▼
read text (binary sniff)      reject files with a NUL byte in the first 4 KB
        ▼
language-aware chunking       tree-sitter on function/class nodes, line-window fallback;
        │                     markdown by heading section
        ▼
set ChunkMetadata             repository_id, repo_name, branch, file_path, language,
        │                     chunk_type, symbol, start/end line, commit_hash
        ▼
content hash (incremental)    skip files whose chunk set is unchanged vs. store.content_hashes()
        ▼
embed (batched, streamed)     embedder.embed_documents in batches of 64
        ▼
ensure_collection + upsert    create collection (cosine) if absent, upsert points
```

Chunking + embedding are **streamed** (batch size 64) so memory stays bounded on
large repos. Progress is reported via the `progress(fraction, message)` callback,
which the API's `IndexingService` writes into the `IndexJob` (SQLite + a Redis
mirror) so the UI can poll either source.

---

## Exclusion rules

Defined in `constants.py` and shared by the walker and chunker:

- **Directories never walked:** `.git`, `node_modules`, `dist`, `build`, `.next`,
  `__pycache__`, `.venv`/`venv`, `vendor`, `target`, `.mypy_cache`, `.ruff_cache`,
  `coverage`, `.pytest_cache`, `.tox`, `.idea`, `.gradle`, `out`, `.cache` (and
  anything starting with `.git`).
- **Excluded extensions:** images/media, archives, compiled/binary artifacts,
  documents/fonts, data blobs/DBs, plus `*.min.js`/`*.min.css`/`*.map`.
- **Excluded filenames:** lockfiles (`package-lock.json`, `yarn.lock`,
  `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`, `Cargo.lock`, `go.sum`, …),
  `.DS_Store`, `Thumbs.db`.
- **Excluded suffixes:** `.lock`, `.lockb`, `.min.js`, `.min.css`.
- **Size:** empty files and anything `> 1 MB` (`MAX_FILE_BYTES`) are skipped.
- **Unknown junk:** files we can't classify to a language *and* without a plausible
  text extension are skipped.
- **`request.paths`** optionally restricts indexing to given path prefixes.

---

## Language-aware chunking

`chunk_file(...)` (in `chunking.py`) picks a strategy per file:

- **Code with a tree-sitter grammar** (`TREE_SITTER_LANGS`, via
  `tree-sitter-language-pack`): split on per-language *definition* node types
  (functions, classes, methods, structs, traits, …). The definition set is scoped
  per language (`DEFINITION_NODE_TYPES_BY_LANG`) to avoid cross-grammar name
  collisions, and container/root nodes (`module`, `program`, `source_file`, …) are
  never chunk boundaries on their own. The chunk's `symbol` is captured when
  detectable.
- **Code without a grammar:** a sliding **line window** (`WINDOW_LINES=60`,
  `WINDOW_OVERLAP=12`).
- **Markdown:** split by heading section → `ChunkType.MARKDOWN`.
- **Other text/docs/config:** classified to `ChunkType.DOC` / `CONFIG` (config-ish
  languages like `json`/`yaml`/`toml`/`dockerfile`) and chunked by window.

Each chunk gets a `content_hash` (for incremental indexing) and a `token_estimate`
(rough `chars / 4`). `detect_language` maps the file extension via `LANG_BY_EXT`.

---

## Embeddings

The `Embedder` is a small `Protocol`: `dim: int`, `name: str`,
`embed_documents(texts)`, `embed_query(text)`. `get_embedder(settings)` returns a
cached singleton selected by `settings.embedding_provider`.

| Backend | Default model | Dim | Notes |
|---|---|---|---|
| `FastEmbedEmbedder` (default) | `BAAI/bge-small-en-v1.5` | **384** | In-process; model downloads on first use and is cached. Uses `query_embed` for queries when available. The real model dim is probed at load and reconciled against `EMBEDDING_DIM`. |
| `OllamaEmbedder` | `nomic-embed-text` | **768** | Serves embeddings over `POST /api/embeddings`. Single-prompt endpoint (documents embedded sequentially). Returns a zero vector on failure (fail-soft). |

> The embedding model defines the vector space. **Changing it (or its dim) requires
> a full re-index.** `EMBEDDING_DIM` must match the model — the code logs a warning
> and uses the *model's* real dim if they disagree.

---

## Qdrant payload schema & filters

One collection (`QDRANT_COLLECTION`, default `tracepilot_chunks`), **cosine**
distance, `dim` from the embedder. Point ids are a deterministic UUID5 of the chunk
id, so re-upserting the same chunk overwrites in place.

**Payload per point:**

| Field | Meaning |
|---|---|
| `chunk_id` | Original chunk id (used as `Evidence.id`). |
| `text` | The chunk body. |
| `content_hash` | For incremental skip. |
| `token_estimate` | Rough token count. |
| `repository_id`, `repo_name`, `branch` | Provenance / scoping. |
| `file_path`, `start_line`, `end_line` | Location → citations. |
| `language`, `chunk_type`, `symbol` | Classification / filtering. |
| `commit_hash` | HEAD at index time. |

**Keyword payload indexes** are created on `repository_id`, `branch`, `chunk_type`,
`language`, `file_path` for fast filtering.

A `RetrievalFilter` maps to Qdrant conditions:

| Filter field | Qdrant condition |
|---|---|
| `repository_ids` | `MatchAny` on `repository_id`. |
| `branch` | `MatchValue` on `branch`. |
| `file_types` (extensions) | mapped to languages via `LANG_BY_EXT`, `MatchAny` on `language`. |
| `chunk_types` | `MatchAny` on `chunk_type`. |
| `path_prefix` | `MatchText` on `file_path` (Qdrant has no native prefix match), then a post-hoc `startswith` check; the store over-fetches 3× to compensate. |

Reads (`search`, `iter_chunks`, `content_hashes`, `count`) all return empty/zero on
an absent or empty collection rather than raising, so the system works before
anything is indexed.

---

## Hybrid fusion & alpha

`Retriever.retrieve(query, tracer)` dispatches on `query.strategy`
(`dense` | `sparse` | `hybrid`, default `hybrid`):

- **Dense** — embed the query, ANN search Qdrant (over-fetching `3×` top_k).
- **Sparse** — BM25 (`rank-bm25` `BM25Okapi`) over up to 2000 chunks scrolled from
  the same filter, using a **code-aware tokenizer** that emits whole tokens plus
  `snake_case` and `camelCase` sub-tokens (so `fetchData_helper` also matches
  `fetch`, `data`, `helper`).
- **Hybrid** — run both legs, **min-max normalize** each leg's scores to `[0,1]`,
  then fuse:

  ```
  fused = HYBRID_ALPHA · dense_norm  +  (1 − HYBRID_ALPHA) · sparse_norm
  ```

  `HYBRID_ALPHA` (default `0.6`) weights dense vs. sparse. If one leg is empty the
  other is returned directly. Final results are truncated to `top_k` with `rank`
  assigned.

Tuning intuition: raise `HYBRID_ALPHA` toward `1.0` for semantic/NL queries; lower
it toward `0.0` when exact identifier matching matters most.

---

## Optional rerank

When `RERANK_ENABLED` (or `query.rerank`) is true and there is evidence, a
**cross-encoder** reranks the fused candidates. It uses `fastembed`'s
`TextCrossEncoder` (`RERANK_MODEL`, default `Xenova/ms-marco-MiniLM-L-6-v2`), loaded
lazily and cached on the `Retriever`. If `fastembed`/the model is unavailable the
reranker is silently disabled (`reranked=False`) and the fused order stands.

---

## Citation assembly

Two helpers turn ranked `Evidence` into what the model and user see — driven from
the **same ordered list** so `[n]` markers always line up:

- **`build_citations(evidence, max_snippet_lines=22)`** — dedupes overlapping
  chunks from the same `(repo, file, start, end)`, assigns stable **1-based**
  indices, and trims each snippet to `max_snippet_lines` (with a "+N more lines"
  note).
- **`pack_context(evidence, max_chars=16000)`** — renders a numbered evidence block
  (`[n] repo · path:start-end (symbol)\n<body>`) bounded to `MAX_CONTEXT_CHARS`, so
  it never blows the model's context window. The last block may be truncated.

The `retriever` node builds both from the merged evidence so the citations the user
receives match the `[n]` markers the synthesizer was shown.

---

## Incremental reindex

With `IndexRequest.incremental=True` (the default) the ingestor pulls
`store.content_hashes(repository_id)` — `{file_path: content_hash}` — once, then
skips any file whose first chunk hash matches the stored marker. Any edit changes at
least one chunk's content hash, which flips the file back to "re-index". Unchanged
files still contribute to the language histogram and byte totals so stats stay
accurate.

Use a **full** reindex (`incremental=False`) when you change the embedding model or
dim, or want to rebuild from scratch. `make seed` indexes the demo repo with
`incremental=False` for a clean first build. To drop a repository entirely, call
`QdrantStore.delete_repository(repository_id)`.
