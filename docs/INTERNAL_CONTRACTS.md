# TracePilot — Internal Package Contracts (authoritative)

This file is the single source of truth for the **public interface** of every
package. All implementations MUST conform to these signatures so the monorepo
stays coherent. Import the canonical models from `tracepilot_shared.models`
(already implemented — read `packages/shared/tracepilot_shared/models/`).

Python module names: `tracepilot_shared`, `tracepilot_prompts`,
`tracepilot_retrieval`, `tracepilot_tooling`, `tracepilot_agent`,
`tracepilot_evals`, `tracepilot_api`.

Conventions everywhere:
- `from tracepilot_shared.config import get_settings` for config.
- `from tracepilot_shared.logging import get_logger` for logging.
- `from tracepilot_shared.telemetry import Tracer` for tracing; wrap each
  meaningful sub-step with `with tracer.span(name, type=..., input=...) as sp:`
  and call `sp.update(output=...)`. Span `type` ∈ {"span","generation","tool","retrieval"}.
- Never run destructive shell commands. All file/process access is sandboxed.
- Fail soft: surface partial results + warnings instead of raising to the user.

---

## tracepilot_shared (DONE — do not modify, just import)
Models (all in `tracepilot_shared.models`): `Workspace, WorkspaceCreate, Repository,
RepositoryConnectRequest, RepositoryStats, IndexRequest, IndexJob, RetrievalQuery,
RetrievalFilter, RetrievalResult, RetrievalStrategy, ChunkMetadata, CodeChunk,
Evidence, Citation, ChatMessage, ChatRequest, ChatResponse, NextAction, DebugRequest,
DebugResponse, RootCauseCandidate, FixPlan, DiffReviewRequest, DiffReviewResponse,
ToolName, ToolSpec, ToolCall, ToolResult, EvalMetric, EvalScore, EvalResult,
EvalExample, EvalRunSummary, TraceSummary` plus enums `ChatMode, IntentType,
ChunkType, RepoStatus, JobStatus, Confidence`.
Also: `tracepilot_shared.ids.new_id(prefix)`, `tracepilot_shared.telemetry.{Tracer,
TraceRecord, list_traces, load_trace, get_langfuse}`.

---

## tracepilot_prompts
File-backed Jinja2 templates in `tracepilot_prompts/templates/*.jinja`.
```python
from tracepilot_prompts import render, load_prompt, available_prompts
render(name: str, **context) -> str   # render templates/<name>.jinja with context
load_prompt(name: str) -> str          # raw template text
available_prompts() -> list[str]
```
Required templates (filenames without extension): `system_preamble`, `router`,
`retrieval_planner`, `code_analyst`, `action_planner`, `synthesizer`, `judge`,
`debug_synthesizer`, `change_review`, `fix_plan`, `onboard`.
Each instructs the model to return STRICT JSON where structured output is needed
(router, retrieval_planner, action_planner, judge, debug, change_review). Templates
take variables like `question`, `mode`, `evidence`, `tools`, `history`, `analysis`.

---

## tracepilot_tooling
```python
from tracepilot_tooling import ToolContext, execute_tool, get_tool_specs, get_registry
@dataclass
class ToolContext:
    workspace_root: str          # absolute path the tool is confined to
    settings: Settings
    timeout_s: int = 30
    max_output_bytes: int = 64000
    extra_allowlist: list[str] = field(default_factory=list)

execute_tool(call: ToolCall, ctx: ToolContext, tracer: Tracer | None = None) -> ToolResult
get_tool_specs() -> list[ToolSpec]
get_registry() -> dict[ToolName, "Tool"]
```
Each tool is a class with `name: ToolName`, `spec: ToolSpec`, and
`run(self, args: dict, ctx: ToolContext) -> ToolResult`. Tools: `repo_search`
(ripgrep, fallback python walk), `read_file` (bounded), `dep_tree` (import/dep map),
`run_tests` (pytest in subprocess), `run_lint` (ruff), `git_diff` (GitPython/subprocess),
`static_analysis` (lightweight AST/heuristics). Guardrails (shared helper
`tracepilot_tooling.sandbox`): `safe_path(ctx, rel) -> Path` raises `SandboxError` on
escape; subprocess calls use `timeout`, capture+truncate to `max_output_bytes`, and a
strict command allowlist (no rm/mv/curl/write). Every tool returns a `ToolResult`.

---

## tracepilot_retrieval
```python
from tracepilot_retrieval import (
    get_embedder, get_qdrant_store, Retriever, Ingestor, build_citations, pack_context,
)
get_embedder(settings=None) -> Embedder        # cached singleton
get_qdrant_store(settings=None) -> QdrantStore  # cached singleton
build_citations(evidence: list[Evidence], max_snippet_lines: int = 22) -> list[Citation]
pack_context(evidence: list[Evidence], max_chars: int = 16000) -> str
```
`Embedder` (protocol): `dim: int`, `name: str`, `embed_documents(texts) -> list[list[float]]`,
`embed_query(text) -> list[float]`. Implementations: `FastEmbedEmbedder` (default,
`fastembed`), `OllamaEmbedder`. Factory picks by `settings.embedding_provider`.

`QdrantStore`:
```python
ensure_collection(dim: int) -> None
upsert(chunks: list[CodeChunk], vectors: list[list[float]]) -> None
search(vector: list[float], flt: RetrievalFilter, top_k: int) -> list[Evidence]
iter_chunks(flt: RetrievalFilter, limit: int = 2000) -> list[Evidence]  # for BM25/sparse
delete_repository(repository_id: str) -> None
count(repository_id: str | None = None) -> int
content_hashes(repository_id: str) -> dict[str, str]   # file_path -> content_hash, for incremental
```
`Retriever(store, embedder, settings)`:
```python
retrieve(query: RetrievalQuery) -> RetrievalResult
```
Implements: dense (Qdrant), sparse (BM25 via `rank-bm25` over `iter_chunks`), hybrid
(weighted-RRF / score fusion using `settings.hybrid_alpha`), optional rerank
(cross-encoder via fastembed `TextCrossEncoder` if `settings.rerank_enabled`).

`Ingestor(store, embedder, settings)`:
```python
ingest(repo: Repository, request: IndexRequest,
       progress: Callable[[float, str], None] | None = None) -> RepositoryStats
```
Pipeline: clone/open repo (GitPython) → walk files (exclude `.git, node_modules, dist,
build, .next, __pycache__, .venv, vendor, *.lock, binaries, >1MB`) → language-aware
chunking (code via `tree-sitter-language-pack` splitting on function/class nodes with a
line-window fallback; markdown by heading sections) → set `ChunkMetadata` (symbol,
lines, language, chunk_type, commit_hash from `repo.head.commit.hexsha`) →
content-hash for incremental (skip unchanged via `store.content_hashes`) → embed →
`ensure_collection` + `upsert`. Return `RepositoryStats`.

---

## tracepilot_agent
```python
from tracepilot_agent import Orchestrator, build_graph
from tracepilot_agent.runtime import RepoLocator  # Protocol

class RepoLocator(Protocol):
    def resolve(self, repository_id: str) -> str | None: ...   # absolute local path
    def name(self, repository_id: str) -> str: ...

class Orchestrator:
    def __init__(self, retriever: Retriever, repo_locator: RepoLocator, settings=None): ...
    def chat(self, req: ChatRequest) -> ChatResponse
    def debug(self, req: DebugRequest) -> DebugResponse
    def review(self, req: DiffReviewRequest) -> DiffReviewResponse
```
Models: `tracepilot_agent.models.get_llm(role: Literal["gen","reason"]) -> ChatOllama`
and `complete(prompt: str, role="gen", want_json=False) -> str | dict` (robust JSON
extraction + retry; on model error returns a safe fallback and records a warning).

`AgentState` (TypedDict in `tracepilot_agent.state`): fields include `request, mode,
intent, plan(list[str]), queries(list[RetrievalQuery]), evidence(list[Evidence]),
citations(list[Citation]), analysis(str), tool_calls(list[ToolCall]),
tool_results(list[ToolResult]), answer(str), confidence, next_actions, warnings,
errors, needs_tools(bool), iterations(int), tracer(Tracer), settings, retriever,
repo_locator`. The LangGraph graph nodes live in `tracepilot_agent/nodes/`:
`router.py, retrieval_planner.py, retriever.py, code_analyst.py, action_planner.py,
tool_executor.py, synthesizer.py, judge.py`. `build_graph()` wires them with
conditional edges: router → retrieval_planner → retriever → code_analyst →
action_planner →(needs_tools? tool_executor → code_analyst : synthesizer) → judge → END.
Bound tool iterations (≤2). Each node wrapped in a `tracer.span`. The Orchestrator
builds the initial state, invokes the compiled graph, and maps the final state into the
appropriate response model. `debug` and `review` reuse the same graph with mode set and
a specialized synthesizer prompt (`debug_synthesizer` / `change_review`).

---

## tracepilot_evals
```python
from tracepilot_evals import evaluate_chat, run_dataset, load_default_dataset
evaluate_chat(req: ChatRequest, resp: ChatResponse) -> EvalResult   # online, heuristic (+optional LLM judge)
run_dataset(examples: list[EvalExample], orchestrator) -> EvalRunSummary  # offline
load_default_dataset() -> list[EvalExample]   # from tracepilot_evals/datasets/default.json
```
Metrics (return `EvalScore` each, 0..1): `grounding` (answer claims supported by cited
evidence — heuristic overlap + optional LLM judge), `relevance` (answer addresses the
question), `completeness` (required sections present: answer, ≥1 citation, next_actions,
confidence), `tool_success` (fraction of tool calls that succeeded; 1.0 if none needed),
`retrieval_quality` (evidence non-empty, scores above threshold, expected files cited in
offline mode). When `trace_id` present, push scores to Langfuse via
`Tracer`/`get_langfuse`. Offline `run_dataset` runs each example through the
orchestrator and aggregates `metric_averages` + `pass_rate`.

---

## apps/api (tracepilot_api)
Entry: `tracepilot_api.main:app` (FastAPI). On startup (lifespan) build singletons and
store on `app.state`: `MetadataStore`, embedder, Qdrant store, `Retriever`, `Ingestor`,
`Orchestrator`. CORS from `settings.cors_origin_list`. Global exception handler returns
`{"error": {"type","message"}}`. Mount routers under no prefix (paths are absolute).

`core/store.py` — `MetadataStore` (SQLite via stdlib `sqlite3`, JSON columns). Methods:
`create_workspace, get_workspace, list_workspaces, create_repository, get_repository,
list_repositories(workspace_id), update_repository, create_job, get_job, update_job,
list_jobs(repository_id)`. All return/accept shared Pydantic models. Thread-safe
(check_same_thread=False + lock).

`core/runtime.py` — `ApiRepoLocator(store)` implements `RepoLocator`.
`core/deps.py` — FastAPI dependency providers reading from `app.state`.
`services/` — `workspace_service`, `repository_service` (connect: clone git URL into
`settings.workspaces_dir` or validate local path), `indexing_service` (run `Ingestor`
in a `BackgroundTasks`/thread, stream progress into the `IndexJob` via store + Redis).

Routers (exact paths):
| Method | Path | Body → Response |
|---|---|---|
| GET | `/health` | → `{status, services:{...}}` |
| POST | `/workspaces` | `WorkspaceCreate` → `Workspace` |
| GET | `/workspaces` | → `list[Workspace]` |
| GET | `/workspaces/{id}` | → `Workspace` |
| POST | `/repositories/connect` | `RepositoryConnectRequest` → `Repository` |
| GET | `/workspaces/{id}/repositories` | → `list[Repository]` |
| GET | `/repositories/{id}` | → `Repository` |
| POST | `/repositories/{id}/index` | `IndexRequest` → `IndexJob` |
| GET | `/repositories/{id}/status` | → `{repository: Repository, job: IndexJob|null}` |
| POST | `/chat/query` | `ChatRequest` → `ChatResponse` |
| POST | `/investigate/debug` | `DebugRequest` → `DebugResponse` |
| POST | `/review/diff` | `DiffReviewRequest` → `DiffReviewResponse` |
| GET | `/traces` | `?limit&workflow` → `list[TraceSummary]` |
| GET | `/traces/{id}` | → `TraceRecord` |
| GET | `/evals` | → `{recent: list[EvalResult], summary: {...}}` |
| POST | `/evals/run` | `{dataset?}` → `EvalRunSummary` |
| GET | `/tools` | → `list[ToolSpec]` |

`/chat/query` flow: create `Tracer`, build `Orchestrator` (from app.state),
`resp = orchestrator.chat(req)`, then `evaluate_chat(req, resp)` (best-effort, push to
trace), return `resp` with `trace_id` set.

---

## apps/web (Next.js 14 App Router, TS, Tailwind)
Base URL: `process.env.NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`).
`lib/api.ts` typed client; `lib/types.ts` mirrors the Pydantic models (camelCase NOT
required — keep snake_case to match JSON). Pages: `/` (workspace + repo dashboard),
`/repositories/[id]` (overview: stats, languages, file tree), `/ingestion` (connect +
index controls + job progress), `/chat` (mode selector ask/onboard/debug/change_review/
fix_plan, chat panel, evidence drawer, trace drawer), `/evaluations` (metric summary +
recent eval results), `/settings` (model + retrieval config display). Layout: left
`Sidebar` (workspaces/repos), top `Topbar` (ingestion status + settings). Dark, technical
internal-tool aesthetic (not marketing). Components wire to the real API but degrade
gracefully when the backend is empty.
```ts
// lib/types.ts must export: Workspace, Repository, ChatRequest, ChatResponse,
// Evidence, Citation, NextAction, DebugResponse, DiffReviewResponse, TraceSummary,
// EvalResult, ToolSpec, IndexJob — matching the JSON field names above.
```
