"""Retrieval-planner node — decompose the request into focused vector queries.

Contract emitted by the prompt::

    {"queries": [{"query": str, "strategy": "hybrid|dense|sparse",
                  "top_k": int, "rationale": str}],
     "filter": {"file_types": [str]|null, "path_prefix": str|null,
                "chunk_types": [str]|null}}

We coerce each planned query into a :class:`RetrievalQuery`, force the request's
``repository_ids`` / ``branch`` onto every filter (so the model can't widen scope
past what the user asked for), and apply a top-level filter when the planner gave
one. If the model is unavailable we fall back to a single hybrid query over the
raw request so retrieval still runs.
"""

from __future__ import annotations

from tracepilot_prompts import render
from tracepilot_shared.models import RetrievalQuery

from ..models import complete
from ..state import AgentState
from . import _common as C

_MAX_QUERIES = 4


def retrieval_planner_node(state: AgentState) -> dict:
    tracer = state["tracer"]
    settings = state.get("settings")
    repo_ids = state.get("repository_ids", [])
    branch = state.get("branch")
    default_top_k = int(getattr(settings, "retrieval_top_k", 8)) if settings else 8
    warnings = list(state.get("warnings", []))

    with tracer.span("retrieval_planner", type="generation", input={"intent": state.get("intent")}) as sp:
        prompt = render(
            "retrieval_planner",
            question=state["request"],
            intent=state.get("intent"),
            history=state.get("history", []),
        )
        parsed = complete(prompt, role="reason", want_json=True, settings=settings)
        C.warn_if_degraded(parsed, "retrieval_planner", warnings)

        queries: list[RetrievalQuery] = []
        plan: list[str] = []
        top_filter = parsed.get("filter") if isinstance(parsed, dict) else None

        raw_queries = parsed.get("queries") if isinstance(parsed, dict) else None
        if isinstance(raw_queries, list):
            for raw in raw_queries[:_MAX_QUERIES]:
                q = C.coerce_query(
                    raw,
                    repository_ids=repo_ids,
                    branch=branch,
                    default_top_k=default_top_k,
                )
                if q is None:
                    continue
                # A per-query filter is rare; honor a planner-level filter when the
                # query didn't carry its own narrowing fields.
                if top_filter is not None and not _query_has_explicit_filter(raw):
                    q = q.model_copy(update={"filter": C.coerce_filter(top_filter, repo_ids, branch)})
                queries.append(q)
                rationale = C.as_str(raw.get("rationale")).strip() if isinstance(raw, dict) else ""
                plan.append(f"[{q.strategy}] {q.query}" + (f" — {rationale}" if rationale else ""))

        if not queries:
            # Fallback: search the raw request directly so the graph still grounds.
            fallback = RetrievalQuery(
                query=state["request"][:512],
                strategy="hybrid",
                top_k=default_top_k,
                filter=C.coerce_filter(None, repo_ids, branch),  # type: ignore[arg-type]
            )
            queries = [fallback]
            plan = [f"[hybrid] {fallback.query}"]
            if not any(w.startswith("retrieval_planner") for w in warnings):
                warnings.append("retrieval_planner: used fallback query over raw request")

        sp.update(output={"n_queries": len(queries), "plan": plan})

    return {"queries": queries, "plan": plan, "warnings": warnings}


def _query_has_explicit_filter(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    f = raw.get("filter")
    if not isinstance(f, dict):
        return False
    return any(f.get(k) for k in ("file_types", "path_prefix", "chunk_types"))
