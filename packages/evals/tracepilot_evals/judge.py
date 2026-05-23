"""Optional LLM-as-judge for grounding + relevance.

When ``settings`` permit (Langfuse/judge enabled and a local model is reachable)
this module asks the reasoning model to score the answer against its cited
evidence using the shared ``judge`` prompt template, then maps the model's
``grounding``/``relevance`` numbers onto :class:`EvalScore` objects. **Any** error
— missing template, missing model, unparseable JSON, import failure — falls back
to the deterministic heuristics in :mod:`tracepilot_evals.metrics`, so the judge
can never make an eval run worse than the heuristic baseline.

The judge only owns the two subjective metrics (grounding, relevance). The
structural metrics (completeness, tool_success, retrieval_quality) are always
computed heuristically — they are objective and a model judgment would add noise.
"""

from __future__ import annotations

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    ChatRequest,
    ChatResponse,
    Citation,
    EvalMetric,
    EvalScore,
)

from . import metrics as M

log = get_logger("evals.judge")


def judge_enabled(settings: Settings | None = None) -> bool:
    """Whether the LLM judge should be attempted.

    Gated on Langfuse being enabled as the project's "use the models for eval"
    switch (the judge shares the reasoning model with the agent graph). Callers
    can still force the heuristic path by passing ``use_judge=False`` upstream.
    """
    s = settings or get_settings()
    return bool(s.langfuse_enabled)


def _clamp01(value: object, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _citation_view(citations: list[Citation]) -> list[dict]:
    """Shape citations for the ``_macros.evidence_block`` macro the judge prompt uses."""
    return [
        {
            "index": c.index,
            "repo": c.repository,
            "file_path": c.file_path,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "snippet": c.snippet,
        }
        for c in citations
    ]


def judge_grounding_relevance(
    req: ChatRequest,
    resp: ChatResponse,
    *,
    settings: Settings | None = None,
) -> dict[EvalMetric, EvalScore] | None:
    """Score grounding + relevance via the LLM judge.

    Returns a mapping for the two metrics, or ``None`` if the judge is disabled,
    unreachable, or returned an unusable result (signalling the caller to use the
    heuristics). Never raises.
    """
    settings = settings or get_settings()
    if not judge_enabled(settings):
        return None
    # Nothing to judge if there is no answer or no evidence to judge it against.
    if not (resp.answer or "").strip() or not resp.citations:
        return None

    try:
        from tracepilot_agent.models import complete, is_degraded
        from tracepilot_prompts import render
    except Exception as exc:  # pragma: no cover - optional deps
        log.debug("LLM judge unavailable (import): %s", exc)
        return None

    try:
        prompt = render(
            "judge",
            question=req.message,
            mode=str(req.mode),
            evidence=_citation_view(resp.citations),
            answer=resp.answer,
        )
    except Exception as exc:
        log.debug("judge prompt render failed: %s", exc)
        return None

    try:
        parsed = complete(prompt, role="reason", want_json=True, settings=settings)
    except Exception as exc:  # complete() is fail-soft, but belt-and-suspenders
        log.debug("judge completion raised: %s", exc)
        return None

    if is_degraded(parsed) or not isinstance(parsed, dict):
        log.debug("judge model degraded; falling back to heuristics")
        return None

    issues = parsed.get("issues") or []
    issue_note = "; ".join(str(i) for i in issues[:3]) if isinstance(issues, list) else ""

    g = _clamp01(parsed.get("grounding"))
    r = _clamp01(parsed.get("relevance"))
    g_rationale = f"LLM judge grounding={g:.2f}" + (f" — {issue_note}" if issue_note else "")
    r_rationale = f"LLM judge relevance={r:.2f}" + (f" — {issue_note}" if issue_note else "")

    return {
        EvalMetric.GROUNDING: EvalScore(
            metric=EvalMetric.GROUNDING,
            score=round(g, 4),
            passed=g >= M.THRESHOLDS[EvalMetric.GROUNDING],
            rationale=g_rationale,
        ),
        EvalMetric.RELEVANCE: EvalScore(
            metric=EvalMetric.RELEVANCE,
            score=round(r, 4),
            passed=r >= M.THRESHOLDS[EvalMetric.RELEVANCE],
            rationale=r_rationale,
        ),
    }


def score_with_judge(
    req: ChatRequest,
    resp: ChatResponse,
    *,
    settings: Settings | None = None,
    expected_files: list[str] | None = None,
    expected_keywords: list[str] | None = None,
    use_judge: bool = True,
) -> list[EvalScore]:
    """Full metric set, using the LLM judge for grounding/relevance when possible.

    Always returns all five metrics in the canonical order. Structural metrics are
    heuristic; grounding/relevance use the judge when ``use_judge`` and the judge
    is available, else fall back to the deterministic heuristics. This is the
    single entry point both the online and offline paths call.
    """
    settings = settings or get_settings()
    base = M.all_metrics(
        req,
        resp,
        expected_files=expected_files,
        expected_keywords=expected_keywords,
    )

    if not use_judge:
        return base

    judged = judge_grounding_relevance(req, resp, settings=settings)
    if not judged:
        return base

    # Replace only the two judged metrics, preserving order.
    out: list[EvalScore] = []
    for score in base:
        out.append(judged.get(score.metric, score))
    return out


__all__ = ["judge_enabled", "judge_grounding_relevance", "score_with_judge"]
