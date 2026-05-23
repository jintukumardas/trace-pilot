"""Retriever node — execute the planned queries and assemble grounding context.

Runs ``state['retriever'].retrieve(query, tracer)`` for each planned
:class:`RetrievalQuery`, merges and dedupes the evidence across queries
(keeping the best score per chunk), then derives the user-facing
:class:`Citation` list and a budget-bounded packed context string. Citations and
packed context are built from the *same* ordered evidence list so the ``[n]``
markers the model sees match the citations the user receives.

This node never calls the LLM. It fails soft: a retriever that raises on one
query degrades to fewer results rather than failing the run.
"""

from __future__ import annotations

from tracepilot_retrieval import build_citations, pack_context

from ..state import AgentState
from . import _common as C

_MAX_EVIDENCE = 12  # cap merged evidence so prompts stay bounded


def retriever_node(state: AgentState) -> dict:
    tracer = state["tracer"]
    retriever = state.get("retriever")
    queries = state.get("queries", [])
    settings = state.get("settings")
    max_chars = int(getattr(settings, "max_context_chars", 16000)) if settings else 16000
    warnings = list(state.get("warnings", []))

    with tracer.span("retriever", type="retrieval", input={"n_queries": len(queries)}) as sp:
        groups: list[list] = []
        if retriever is None:
            warnings.append("retriever: no retriever injected; skipping retrieval")
        else:
            for q in queries:
                try:
                    result = retriever.retrieve(q, tracer=tracer)
                    groups.append(list(result.evidence))
                except Exception as exc:  # fail soft per query
                    warnings.append(f"retriever: query failed ({type(exc).__name__})")

        evidence = C.merge_evidence(groups)[:_MAX_EVIDENCE]
        citations = build_citations(evidence)
        context = pack_context(evidence, max_chars=max_chars)

        sp.update(output={"n_evidence": len(evidence), "n_citations": len(citations)})

    if not evidence:
        warnings.append("retriever: no evidence retrieved for any query")

    return {
        "evidence": evidence,
        "citations": citations,
        "context": context,
        "warnings": warnings,
    }
