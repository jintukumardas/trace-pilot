"""Code-analyst node — produce internal free-text reasoning over the evidence.

This is the one node that emits *free text* (not JSON). Its analysis feeds both
the action planner (which decides whether tools are needed) and the synthesizer
(which writes the user-facing answer). The node runs after every tool loop, so on
the second pass it folds in the tool results too.

On a degraded model it records a short factual note instead of fabricating
analysis, keeping the downstream synthesizer grounded purely on the evidence.
"""

from __future__ import annotations

from tracepilot_prompts import render

from ..models import complete, is_degraded
from ..state import AgentState
from . import _common as C


def code_analyst_node(state: AgentState) -> dict:
    tracer = state["tracer"]
    citations = state.get("citations", [])
    tool_results = state.get("tool_results", [])
    warnings = list(state.get("warnings", []))

    with tracer.span(
        "code_analyst",
        type="generation",
        input={"n_evidence": len(citations), "iteration": state.get("iterations", 0)},
    ) as sp:
        prompt = render(
            "code_analyst",
            question=state["request"],
            intent=state.get("intent"),
            mode=state.get("mode"),
            evidence=C.evidence_view(citations),
            tool_results=tool_results,
        )
        result = complete(prompt, role="reason", want_json=False, settings=state.get("settings"))

        if is_degraded(result):
            warnings.append("code_analyst: model unavailable; analysis based on evidence only")
            analysis = (
                "Automated analysis unavailable (model offline). "
                f"{len(citations)} evidence chunk(s) retrieved; the answer below is "
                "assembled directly from them. Confidence: low"
            )
        else:
            analysis = C.as_str(result).strip()

        sp.update(output={"analysis_chars": len(analysis)})

    return {"analysis": analysis, "warnings": warnings}
