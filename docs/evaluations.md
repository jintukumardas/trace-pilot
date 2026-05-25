# Evaluations

TracePilot scores every grounded answer so you can tell whether the system is
actually working — not just that it returned text. Scoring happens **online** (on
every `/chat/query`, best-effort) and **offline** (against a labeled dataset), and
the scores land in Langfuse and the built-in trace store.

Public surface (`tracepilot_evals`, see
[`INTERNAL_CONTRACTS.md`](INTERNAL_CONTRACTS.md)):

```python
from tracepilot_evals import evaluate_chat, run_dataset, load_default_dataset

evaluate_chat(req: ChatRequest, resp: ChatResponse) -> EvalResult     # online
run_dataset(examples: list[EvalExample], orchestrator) -> EvalRunSummary  # offline
load_default_dataset() -> list[EvalExample]                            # tracepilot_evals/datasets/default.json
```

---

## The five metrics

Each metric returns an `EvalScore` in `[0, 1]` with a `passed` flag and a
`rationale`. The metrics are the `EvalMetric` enum in `tracepilot_shared`:

| Metric | Question it answers | How it's computed |
|---|---|---|
| **grounding** | Are the answer's claims supported by the cited evidence? | Heuristic token/overlap between the answer and the cited snippets, plus a check that the answer actually uses `[n]` markers. Optionally refined by an LLM judge (the graph's `judge` node) when the model is available. |
| **relevance** | Does the answer address the question? | Heuristic overlap between the question and the answer (keyword/semantic signal); LLM-judge refinement when available. |
| **completeness** | Are the required sections present and useful? | Structural: a non-empty `answer`, **≥1 citation**, ≥1 `next_action`, and a `confidence` band. Each present component contributes to the score. |
| **tool_success** | Did invoked tools succeed and help? | Fraction of `tools_used` that returned `ok`. **1.0 when no tools were needed** (not penalized for not using tools). |
| **retrieval_quality** | Did retrieval surface relevant chunks? | Evidence is non-empty, top scores are above threshold, and — in **offline** mode — the example's `expected_files` appear among the citations. |

The graph's `judge` node already writes `grounding` / `relevance` / `completeness`
onto the trace during a run; `evaluate_chat` computes the full five-metric set for
the response envelope and pushes them onto the same `trace_id`. When the judge model
is offline, the `judge` node falls back to a conservative heuristic so the trace
still carries signal.

`EvalResult.overall` is the mean of its scores; an example/result "passes" when its
scores clear the pass threshold (default `0.6`).

---

## Online vs. offline

| | Online | Offline |
|---|---|---|
| **Trigger** | Every `POST /chat/query` (best-effort) | `POST /evals/run`, `make eval`-style CLI, or `/evaluations` in the UI |
| **Input** | The live `ChatRequest` + `ChatResponse` | A labeled `EvalExample` dataset, each run through the orchestrator |
| **Entry point** | `evaluate_chat(req, resp)` | `run_dataset(examples, orchestrator)` |
| **Labels** | None (reference-free heuristics + optional judge) | `expected_files` / `expected_keywords` per example |
| **Failure policy** | Never fails the request; on error adds an `"online evaluation skipped"` warning | Guarded; a single example failure doesn't abort the run |
| **Output** | Scores merged onto the trace (Langfuse + Redis) | `EvalRunSummary` with `metric_averages` + `pass_rate` |

The online path lives in the chat route's `_run_online_eval` → `_push_scores`: it
mirrors scores to Langfuse via `get_langfuse().score(...)` **and** folds them into
the persisted `TraceRecord` so `GET /evals` and the trace UI show them even without
Langfuse.

---

## Dataset format

The default dataset is `packages/evals/tracepilot_evals/datasets/default.json`, a
list of `EvalExample` objects:

```json
[
  {
    "id": "ledger-transfer-flow",
    "question": "How does a transfer work and what invariants must it preserve?",
    "mode": "ask",
    "repository_id": null,
    "expected_files": ["service.py", "docs/transfers.md", "money.py"],
    "expected_keywords": ["minor units", "InsufficientFunds", "conservation"],
    "notes": "Should explain the transfer guard and the no-negative-balance invariant."
  }
]
```

| Field | Meaning |
|---|---|
| `id` | Stable example id. |
| `question` | The prompt sent to the orchestrator. |
| `mode` | `ChatMode` (`ask` / `onboard` / `debug` / `change_review` / `fix_plan`). |
| `repository_id` | Optional repo to scope retrieval to (left `null` to search the workspace). |
| `expected_files` | Files a good answer should cite — used by `retrieval_quality`. |
| `expected_keywords` | Phrases a good answer should contain — used by relevance/grounding signals. |
| `notes` | Free-text rationale for maintainers. |

> `expected_files` are matched against citation `file_path`s, so list the basename
> or repo-relative path that retrieval will produce.

---

## Running evaluations

**Offline, via the API** (uses the live orchestrator):

```bash
curl -X POST http://localhost:8000/evals/run -H 'content-type: application/json' -d '{}'
# → EvalRunSummary { dataset, n, metric_averages, pass_rate, results[] }
```

**Recent online scores:**

```bash
curl http://localhost:8000/evals
# → { recent: EvalResult[], summary: { n, metric_averages, pass_rate, overall } }
```

**From the UI:** the `/evaluations` page shows per-metric cards and recent results.

> The offline run requires the `tracepilot_evals` package to be installed (it is,
> via `make install`); the endpoint returns a clear `503` if it isn't, and a `400`
> if the dataset fails to load.

---

## How scores land in Langfuse

1. During a run, the **`judge`** node calls `tracer.score(name, value)` for
   grounding/relevance/completeness — mirrored to the Langfuse trace and stored on
   the Redis `TraceRecord`.
2. After the response is built, the chat route runs `evaluate_chat` and calls
   `get_langfuse().score(trace_id=..., name=..., value=...)` for the full metric
   set, then `flush()`. It also folds the scores into the persisted trace so the
   built-in `/traces` and `/evals` views show them.
3. In Langfuse (`http://localhost:3001`) the scores appear on each trace and roll
   up into score dashboards over time.

Everything is best-effort: if Langfuse is disabled or unreachable, scores still
persist to Redis and surface through the API.

---

## Extending

**Add a metric**
1. Add a member to `EvalMetric` in `tracepilot_shared.models.evals`
   *(shared is frozen — coordinate this change with the platform owners)*.
2. Implement its scorer in `tracepilot_evals`, returning an `EvalScore`, and include
   it in both `evaluate_chat` and the offline aggregation.
3. If the metric needs the judge model, wire it through the graph's `judge` node and
   emit it via `tracer.score(...)`.
4. The API's `/evals` summary aggregates any `EvalMetric` it finds on traces, so new
   metrics surface automatically once they're being written.

**Add a dataset example**
1. Append an `EvalExample` object to
   `packages/evals/tracepilot_evals/datasets/default.json` (or ship a new dataset
   file and select it via the `dataset` field of `POST /evals/run`).
2. Pick realistic `expected_files`/`expected_keywords` so `retrieval_quality` and
   relevance have ground truth to check against.
3. Re-run `POST /evals/run` and confirm the example contributes to
   `metric_averages` / `pass_rate`.
