"""Judge node — score the synthesized answer against the evidence.

Contract emitted by the prompt::

    {"grounding": 0..1, "relevance": 0..1, "completeness": 0..1,
     "confidence": "low|medium|high", "issues": [str]}

The scores are written to the trace via ``tracer.score(...)`` (which mirrors to
Langfuse when configured) and stashed on the state so the orchestrator can surface
them. The judge is advisory: it never overwrites the answer. When the model is
unavailable we fall back to a cheap heuristic completeness/grounding estimate so
the trace still carries signal.
"""

from __future__ import annotations

from tracepilot_prompts import render

from ..models import complete, is_degraded
from ..state import AgentState
from . import _common as C


def judge_node(state: AgentState) -> dict:
    tracer = state["tracer"]
    citations = state.get("citations", [])
    answer = state.get("answer", "")
    warnings = list(state.get("warnings", []))

    with tracer.span("judge", type="generation", input={"n_evidence": len(citations)}) as sp:
        prompt = render(
            "judge",
            question=state["request"],
            mode=state.get("mode"),
            evidence=C.evidence_view(citations),
            answer=answer,
        )
        parsed = complete(prompt, role="reason", want_json=True, settings=state.get("settings"))

        if is_degraded(parsed):
            scores, issues = _heuristic_scores(state)
            warnings.append("judge: model unavailable; used heuristic scores")
        else:
            scores = {
                "grounding": C.clamp01(parsed.get("grounding")),
                "relevance": C.clamp01(parsed.get("relevance")),
                "completeness": C.clamp01(parsed.get("completeness")),
            }
            issues = C.as_str_list(parsed.get("issues"))

        # Write scores to the trace (mirrors to Langfuse when enabled).
        for name, value in scores.items():
            tracer.score(name, value, comment="; ".join(issues[:3]) if issues else "")

        sp.update(output={**scores, "issues": issues})

    return {"scores": scores, "warnings": warnings}


def _heuristic_scores(state: AgentState) -> tuple[dict[str, float], list[str]]:
    """Cheap, deterministic fallback when the judge model is offline.

    Grounding ≈ presence of citations + inline markers; completeness ≈ required
    sections present. Intentionally conservative.
    """
    citations = state.get("citations", [])
    answer = state.get("answer", "") or ""
    next_actions = state.get("next_actions", [])

    has_evidence = bool(citations)
    has_markers = any(f"[{c.index}]" in answer for c in citations) if citations else False

    grounding = 0.0
    if has_evidence and has_markers:
        grounding = 0.6
    elif has_evidence:
        grounding = 0.3

    completeness = 0.0
    completeness += 0.4 if answer.strip() else 0.0
    completeness += 0.3 if has_evidence else 0.0
    completeness += 0.3 if next_actions else 0.0

    relevance = 0.5 if answer.strip() else 0.0

    issues: list[str] = []
    if not has_evidence:
        issues.append("no evidence retrieved")
    if has_evidence and not has_markers:
        issues.append("answer does not cite the retrieved evidence")
    issues.append("scored heuristically; judge model unavailable")
    return (
        {"grounding": grounding, "relevance": relevance, "completeness": completeness},
        issues,
    )
