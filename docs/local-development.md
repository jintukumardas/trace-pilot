# Local development

Two supported paths:

- **Docker path** — the fastest way to a full, working stack. Recommended for a
  first run and for anyone who just wants to use TracePilot.
- **Native path** — run the API and web app directly on your machine against
  containerized infra (Qdrant/Redis/Ollama/Langfuse). Recommended for backend
  development with hot reload and a debugger.

Run `make help` at any time to see the task menu.

---

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Docker + Compose | recent | Running the stack / infra |
| Python | 3.11+ | API + packages (native path) |
| Node.js | 18.18+ | Web UI (native path) |
| Git | any | Connecting & cloning repositories |
| Ollama models | — | Pulled via `make pull-models` (or a host Ollama) |

The default models (`llama3.1:8b`, `qwen2.5-coder:7b`) run on CPU. A GPU is
optional and speeds up generation considerably.

---

## Docker path

```bash
cp .env.example .env       # configure (defaults work out of the box)
make up                    # build + start everything, detached
make pull-models           # one-time: pull the default Ollama models
make seed                  # connect + index the demo repo, run a sample query
```

Then open:

- Web UI — http://localhost:3000
- API docs — http://localhost:8000/docs
- Langfuse — http://localhost:3001 (`admin@tracepilot.local` / `tracepilot123`)
- Qdrant — http://localhost:6333/dashboard

Useful commands:

```bash
make logs     # tail api + web logs
make down     # stop the stack (keeps volumes/data)
make clean    # stop + remove all volumes (DESTRUCTIVE: wipes index + traces)
```

### Connecting your own repo (Docker)
The API container can connect a repo two ways:

- **By git URL** — works anywhere; the repo is shallow-cloned into the API's data
  volume.
- **By local path** — the path must be visible *inside the container*. The compose
  file mounts a host directory read-only at `/repos`:

  ```bash
  HOST_REPOS_DIR=/abs/path/to/your/repos make up
  # then connect with local_path = /repos/<your-repo-dir>
  ```

---

## Native path

Run infra in Docker, but the API and web app on the host.

### 1. Start infra only

```bash
docker compose up -d qdrant redis ollama langfuse-server langfuse-db
make pull-models
```

### 2. Python environment + packages

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
make install                       # editable-installs all packages in dep order
```

`make install` runs `scripts/install_packages.sh`, which installs
`shared → prompts → tooling → retrieval → agent-graph → evals → api` so internal
`tracepilot-*` dependencies resolve from the editable installs.

### 3. Point the API at host-mapped ports

For native runs the services are reachable on `localhost`. Create `apps/api/.env`
(or export the vars) overriding the container hostnames:

```env
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0
OLLAMA_BASE_URL=http://localhost:11434
LANGFUSE_HOST=http://localhost:3001
DATA_DIR=./.tracepilot
WORKSPACES_DIR=./.tracepilot/workspaces
DATABASE_URL=sqlite:///./.tracepilot/tracepilot.db
```

### 4. Run the API

```bash
make api      # uvicorn tracepilot_api.main:app --reload on :8000
```

### 5. Run the web app

```bash
cd apps/web
npm install
npm run dev   # Next.js on :3000  (or `make web` from the repo root)
```

The web app reads `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`).

### 6. Seed (native)

```bash
python scripts/seed_demo.py     # or `make seed`
```

Because the API runs on the host, the demo repo's absolute path is directly
visible — no mount needed.

---

## Environment variable reference

All settings are read from the environment (see `.env.example`). The most relevant:

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `local` | `local` / `dev` / `prod`. |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `false` | Logging verbosity and format. |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8000` | API bind. |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins. |
| `DATA_DIR` / `WORKSPACES_DIR` | `./.tracepilot[/workspaces]` | Metadata DB + repo clones. |
| `DATABASE_URL` | `sqlite:///./.tracepilot/tracepilot.db` | SQLite metadata store. |
| `REDIS_URL` | `redis://...:6379/0` | Job + trace cache. |
| `QDRANT_URL` / `QDRANT_COLLECTION` | `http://...:6333` / `tracepilot_chunks` | Vector store. |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Model server. |
| `OLLAMA_GEN_MODEL` / `OLLAMA_REASONING_MODEL` | `llama3.1:8b` / `qwen2.5-coder:7b` | Answer / planning models. |
| `MODEL_TEMPERATURE` / `MODEL_NUM_CTX` | `0.1` / `8192` | Decoding params. |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` or `ollama`. |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | `BAAI/bge-small-en-v1.5` / `384` | Must agree. `nomic-embed-text` ⇒ 768. |
| `RETRIEVAL_TOP_K` / `HYBRID_ALPHA` | `8` / `0.6` | Retrieval defaults. |
| `RERANK_ENABLED` | `false` | Cross-encoder rerank. |
| `MAX_CONTEXT_CHARS` | `16000` | Packed-context budget. |
| `TOOL_TIMEOUT_SECONDS` / `TOOL_MAX_OUTPUT_BYTES` | `30` / `64000` | Tool sandbox budgets. |
| `TOOL_ALLOWLIST` | `` | Comma-separated extra allowed path prefixes for tools. |
| `LANGFUSE_ENABLED` / `LANGFUSE_HOST` | `true` / `http://...:3001` | Observability. |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | seeded | Must match `LANGFUSE_INIT_*` in compose. |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Web → API base URL. |

---

## Common workflows

**Index a repository**
1. Create a workspace (UI `/`, or `POST /workspaces`).
2. Connect a repo (`/ingestion`, or `POST /repositories/connect`) by path or git URL.
3. Index it (`POST /repositories/{id}/index`); poll `GET /repositories/{id}/status`.

**Ask a grounded question** — `/chat` in the UI, or `POST /chat/query`. Pick a mode
(ask / onboard / debug / change_review / fix_plan). Citations open in the evidence
drawer; the trace opens in the trace drawer.

**Inspect a trace** — UI `/chat` trace drawer or `GET /traces` / `GET /traces/{id}`;
the full tree (with scores) is also in Langfuse at :3001.

**Run evaluations** — `/evaluations` in the UI, or `POST /evals/run` for the offline
dataset; `GET /evals` shows recent online scores. See [`evaluations.md`](evaluations.md).

---

## Resetting state

| Goal | Command |
|---|---|
| Stop, keep all data | `make down` |
| Wipe everything (vectors, traces, metadata, model cache) | `make clean` (removes Docker volumes) |
| Reset native metadata only | delete `./.tracepilot/tracepilot.db` |
| Drop a single repo's vectors | `QdrantStore(...).delete_repository(repo_id)` (or delete the Qdrant collection) |
| Clear traces | flush the Redis keys `tracepilot:trace:*` (they also auto-expire after 7 days) |

---

## Tests & lint

```bash
make test     # pytest -q
make lint     # ruff check packages apps/api tests
make fmt      # ruff format packages apps/api tests scripts
```

For the web app:

```bash
cd apps/web
npm run lint
npm run typecheck
```

> The sandboxed tooling and graph are designed to be unit-testable without live
> infra: embedders, the Qdrant store, Redis, and Langfuse are all lazily imported
> and fail soft, so tests that don't need them won't pull them in.
