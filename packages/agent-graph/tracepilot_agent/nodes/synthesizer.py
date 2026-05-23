"""Synthesizer node — write the final, grounded output for the active mode.

Dispatches on ``state['mode']`` to the right template + parser:

* ``debug``          → ``debug_synthesizer``  (root-cause + fix plan structure)
* ``change_review``  → ``change_review``      (impact + risk structure)
* everything else    → ``synthesizer``        (answer + next_actions)

Each branch parses the mode's strict-JSON contract into shared models and writes
``answer`` / ``confidence`` / ``next_actions`` (and ``state['debug']`` /
``state['review']`` for the structured modes). When the model is unavailable we
synthesize a clear "model unavailable" answer *grounded on the retrieved
evidence* (it lists the cited files), so the run still returns something useful.
"""

from __future__ import annotations

from typing import Any

from tracepilot_prompts import render
from tracepilot_shared.models import Citation, NextAction

from ..models import complete, is_degraded
from ..state import DEBUG_MODES, REVIEW_MODES, AgentState
from . import _common as C


def synthesizer_node(state: AgentState) -> dict:
    mode = state.get("mode", "ask")
    if mode in DEBUG_MODES:
        return _synthesize_debug(state)
    if mode in REVIEW_MODES:
        return _synthesize_review(state)
    return _synthesize_answer(state)


# --------------------------------------------------------------------------- #
# Standard Q&A / onboarding / fix_plan
# --------------------------------------------------------------------------- #
def _synthesize_answer(state: AgentState) -> dict:
    tracer = state["tracer"]
    citations = state.get("citations", [])
    warnings = list(state.get("warnings", []))

    with tracer.span(
        "synthesizer", type="generation", input={"mode": state.get("mode"), "n_evidence": len(citations)}
    ) as sp:
        prompt = render(
            "synthesizer",
            question=state["request"],
            mode=state.get("mode"),
            history=state.get("history", []),
            evidence=C.evidence_view(citations),
            analysis=state.get("analysis", ""),
            tool_results=state.get("tool_results", []),
        )
        parsed = complete(prompt, role="gen", want_json=True, settings=state.get("settings"))

        if is_degraded(parsed):
            warnings.append("synthesizer: model unavailable; returned evidence-grounded fallback")
            answer = _fallback_answer(state, citations)
            confidence = "low"
            next_actions = _fallback_next_actions(citations)
        else:
            answer = C.as_str(parsed.get("answer")).strip() or _fallback_answer(state, citations)
            confidence = C.coerce_confidence(parsed.get("confidence"), "low" if not citations else "medium")
            next_actions = C.coerce_next_actions(parsed.get("next_actions"))

        sp.update(output={"answer_chars": len(answer), "confidence": confidence})

    return {
        "answer": answer,
        "confidence": confidence,
        "next_actions": next_actions,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Debug mode
# --------------------------------------------------------------------------- #
def _synthesize_debug(state: AgentState) -> dict:
    tracer = state["tracer"]
    citations = state.get("citations", [])
    warnings = list(state.get("warnings", []))

    with tracer.span("debug_synthesizer", type="generation", input={"n_evidence": len(citations)}) as sp:
        prompt = render(
            "debug_synthesizer",
            question=state["request"],
            bug_report=state["request"],
            stack_trace=state.get("stack_trace"),
            reproduction=state.get("reproduction"),
            mode=state.get("mode"),
            evidence=C.evidence_view(citations),
            analysis=state.get("analysis", ""),
            tool_results=state.get("tool_results", []),
        )
        parsed = complete(prompt, role="reason", want_json=True, settings=state.get("settings"))

        if is_degraded(parsed):
            warnings.append("debug_synthesizer: model unavailable; returned evidence-grounded fallback")
            debug = _fallback_debug(state, citations)
        else:
            debug = _coerce_debug(parsed, citations)

        confidence = debug["confidence"]
        # Mirror a readable summary into ``answer`` so generic consumers/UIs and the
        # judge (which scores the answer) have text to work with.
        answer = debug["summary"]

        sp.update(output={"confidence": confidence, "n_candidates": len(debug["root_cause_candidates"])})

    return {
        "answer": answer,
        "confidence": confidence,
        "debug": debug,
        "next_actions": _debug_next_actions(debug),
        "warnings": warnings,
    }


def _coerce_debug(parsed: dict, citations: list[Citation]) -> dict:
    summary = C.as_str(parsed.get("summary")).strip()
    candidates_raw = parsed.get("root_cause_candidates")
    candidates: list[dict] = []
    n_ev = len(citations)
    if isinstance(candidates_raw, list):
        for item in candidates_raw[:5]:
            if not isinstance(item, dict):
                continue
            hyp = C.as_str(item.get("hypothesis")).strip()
            if not hyp:
                continue
            idx = [i for i in _int_list(item.get("evidence_indices")) if 0 <= i < n_ev]
            candidates.append(
                {
                    "hypothesis": hyp,
                    "confidence": C.coerce_confidence(item.get("confidence"), "medium"),
                    "impacted_files": C.as_str_list(item.get("impacted_files")),
                    "reasoning": C.as_str(item.get("reasoning")).strip(),
                    "evidence_indices": idx,
                }
            )
    fix_raw = parsed.get("fix_plan") if isinstance(parsed.get("fix_plan"), dict) else {}
    fix_plan = {
        "steps": C.as_str_list(fix_raw.get("steps")),
        "risks": C.as_str_list(fix_raw.get("risks")),
        "test_strategy": C.as_str_list(fix_raw.get("test_strategy")),
        "rollback": C.as_str(fix_raw.get("rollback")).strip() or None,
    }
    impacted = C.as_str_list(parsed.get("impacted_files"))
    if not impacted:  # derive from candidates if the model omitted the union
        seen: list[str] = []
        for c in candidates:
            for f in c["impacted_files"]:
                if f not in seen:
                    seen.append(f)
        impacted = seen
    return {
        "summary": summary or "The provided evidence does not clearly localize the bug.",
        "root_cause_candidates": candidates,
        "impacted_files": impacted,
        "diagnostic_steps": C.as_str_list(parsed.get("diagnostic_steps")),
        "fix_plan": fix_plan,
        "confidence": C.coerce_confidence(parsed.get("confidence"), "low" if not candidates else "medium"),
    }


def _debug_next_actions(debug: dict) -> list[NextAction]:
    actions: list[NextAction] = []
    for step in debug.get("diagnostic_steps", [])[:3]:
        actions.append(
            NextAction(
                title="Run diagnostic", detail=step, rationale="Confirm or refute a root-cause hypothesis"
            )
        )
    return actions


# --------------------------------------------------------------------------- #
# Change-review mode
# --------------------------------------------------------------------------- #
def _synthesize_review(state: AgentState) -> dict:
    tracer = state["tracer"]
    citations = state.get("citations", [])
    warnings = list(state.get("warnings", []))
    diff = state.get("diff") or ""

    with tracer.span(
        "change_review", type="generation", input={"n_evidence": len(citations), "has_diff": bool(diff)}
    ) as sp:
        prompt = render(
            "change_review",
            question=state["request"],
            title=state.get("title"),
            diff=diff,
            mode=state.get("mode"),
            evidence=C.evidence_view(citations),
            analysis=state.get("analysis", ""),
        )
        parsed = complete(prompt, role="reason", want_json=True, settings=state.get("settings"))

        if is_degraded(parsed):
            warnings.append("change_review: model unavailable; returned evidence-grounded fallback")
            review = _fallback_review(state, citations)
        else:
            review = _coerce_review(parsed, diff)

        answer = review["summary"]
        confidence = review["risk_level"]  # risk band doubles as the response confidence
        sp.update(output={"risk_level": review["risk_level"]})

    return {
        "answer": answer,
        "confidence": confidence,
        "review": review,
        "next_actions": _review_next_actions(review),
        "warnings": warnings,
    }


def _coerce_review(parsed: dict, diff: str) -> dict:
    summary = C.as_str(parsed.get("summary")).strip()
    if not summary:
        summary = (
            "No reviewable change was provided."
            if not diff.strip()
            else "The change could not be summarized from the available evidence."
        )
    return {
        "summary": summary,
        "impact": C.as_str(parsed.get("impact")).strip(),
        "risk_level": C.coerce_confidence(parsed.get("risk_level"), "low" if not diff.strip() else "medium"),
        "affected_areas": C.as_str_list(parsed.get("affected_areas")),
        "suggested_tests": C.as_str_list(parsed.get("suggested_tests")),
    }


def _review_next_actions(review: dict) -> list[NextAction]:
    actions: list[NextAction] = []
    for test in review.get("suggested_tests", [])[:3]:
        actions.append(
            NextAction(title="Add/run test", detail=test, rationale="Cover the change and its edge cases")
        )
    return actions


# --------------------------------------------------------------------------- #
# Fallbacks (used when the model is unreachable) — grounded purely on evidence
# --------------------------------------------------------------------------- #
def _cited_files(citations: list[Citation], limit: int = 6) -> list[str]:
    seen: list[str] = []
    for c in citations:
        loc = f"{c.file_path}:{c.start_line}-{c.end_line}"
        if loc not in seen:
            seen.append(loc)
        if len(seen) >= limit:
            break
    return seen


def _fallback_answer(state: AgentState, citations: list[Citation]) -> str:
    if not citations:
        return (
            "The local model is unavailable and no supporting evidence was retrieved "
            "for this request, so a grounded answer cannot be produced. Verify the "
            "repository is indexed and that the Ollama service is reachable, then retry."
        )
    lines = [
        "The local model is currently unavailable, so this is an evidence-only summary "
        "rather than a synthesized answer.",
        "",
        "The most relevant indexed locations for your request are:",
    ]
    for i, c in enumerate(citations[:6], start=1):
        lines.append(f"- [{i}] {c.repository} · {c.file_path}:{c.start_line}-{c.end_line}")
    lines.append("")
    lines.append("Re-run once Ollama is reachable for a full, cited answer.")
    return "\n".join(lines)


def _fallback_next_actions(citations: list[Citation]) -> list[NextAction]:
    actions = [
        NextAction(
            title="Restore the model service",
            detail="Confirm the Ollama endpoint (OLLAMA_BASE_URL) is up and the configured model is pulled.",
            rationale="The synthesizer requires a reachable local model to produce a grounded answer.",
        )
    ]
    if citations:
        top = citations[0]
        actions.append(
            NextAction(
                title=f"Inspect {top.file_path}",
                detail=f"Open {top.file_path}:{top.start_line}-{top.end_line} — the top-ranked evidence.",
                rationale="It is the most relevant retrieved location for the request.",
            )
        )
    return actions


def _fallback_debug(state: AgentState, citations: list[Citation]) -> dict:
    impacted = [c.file_path for c in citations[:5]]
    return {
        "summary": (
            "The local model is unavailable; this is an evidence-only triage. "
            + (
                "Most relevant locations: " + ", ".join(_cited_files(citations)) + "."
                if citations
                else "No supporting evidence was retrieved."
            )
        ),
        "root_cause_candidates": [],
        "impacted_files": impacted,
        "diagnostic_steps": [
            "Restore the Ollama model service and re-run the debug investigation.",
            *([f"Review {c.file_path}:{c.start_line}-{c.end_line}." for c in citations[:3]]),
        ],
        "fix_plan": {"steps": [], "risks": [], "test_strategy": [], "rollback": None},
        "confidence": "low",
    }


def _fallback_review(state: AgentState, citations: list[Citation]) -> dict:
    return {
        "summary": (
            "The local model is unavailable; the diff could not be reviewed automatically."
            + (
                " Surrounding code of interest: " + ", ".join(_cited_files(citations)) + "."
                if citations
                else ""
            )
        ),
        "impact": "Unknown — automated review unavailable.",
        "risk_level": "medium" if (state.get("diff") or "").strip() else "low",
        "affected_areas": [c.file_path for c in citations[:5]],
        "suggested_tests": ["Re-run the review once the model service is reachable."],
    }


# --------------------------------------------------------------------------- #
def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for v in value:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out
