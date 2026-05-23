"""Offline, dataset-driven evaluation.

``run_dataset`` runs each :class:`EvalExample` through a live ``Orchestrator``
(anything exposing ``chat(ChatRequest) -> ChatResponse``), scores the response
with the **offline** metric variants (label-aware: ``expected_files`` /
``expected_keywords``), and aggregates an :class:`EvalRunSummary` carrying per-
metric averages, an overall pass rate, and the per-example :class:`EvalResult`
list.

Robustness: a crash on one example never aborts the run — it is recorded as a
zero-scored result with a rationale, and the run continues. This mirrors the
platform-wide "fail soft, return partial results" rule so a flaky model or a
missing repo degrades a single row rather than the whole report.
"""

from __future__ import annotations

from typing import Protocol

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    ChatRequest,
    ChatResponse,
    EvalExample,
    EvalMetric,
    EvalResult,
    EvalRunSummary,
    EvalScore,
)

from .judge import score_with_judge

log = get_logger("evals.offline")


class _ChatLike(Protocol):
    """Minimal surface ``run_dataset`` needs from an orchestrator."""

    def chat(self, req: ChatRequest) -> ChatResponse: ...


def _example_to_request(ex: EvalExample) -> ChatRequest:
    """Map a dataset example onto a :class:`ChatRequest` for the orchestrator."""
    return ChatRequest(
        workspace_id="eval",
        repository_ids=[ex.repository_id] if ex.repository_id else [],
        mode=ex.mode,
        message=ex.question,
    )


def _failed_result(ex: EvalExample, reason: str) -> EvalResult:
    """A zero-scored result standing in for an example the orchestrator couldn't answer."""
    scores = [EvalScore(metric=m, score=0.0, passed=False, rationale=reason) for m in EvalMetric]
    return EvalResult(trace_id=None, workflow=str(ex.mode), scores=scores, overall=0.0)


def evaluate_example(
    ex: EvalExample,
    resp: ChatResponse,
    *,
    settings: Settings | None = None,
    use_judge: bool = True,
) -> EvalResult:
    """Score a single example's response with the offline (label-aware) metrics."""
    settings = settings or get_settings()
    req = _example_to_request(ex)
    scores = score_with_judge(
        req,
        resp,
        settings=settings,
        expected_files=ex.expected_files or None,
        expected_keywords=ex.expected_keywords or None,
        use_judge=use_judge,
    )
    overall = round(sum(s.score for s in scores) / len(scores), 4) if scores else 0.0
    return EvalResult(
        trace_id=resp.trace_id,
        workflow=str(ex.mode),
        scores=scores,
        overall=overall,
    )


def run_dataset(
    examples: list[EvalExample],
    orchestrator: _ChatLike,
    *,
    dataset: str = "default",
    settings: Settings | None = None,
    use_judge: bool = True,
) -> EvalRunSummary:
    """Run every example through ``orchestrator.chat`` and aggregate the scores.

    Parameters
    ----------
    examples:      labeled dataset rows.
    orchestrator:  anything with ``chat(ChatRequest) -> ChatResponse``.
    dataset:       name recorded on the summary (for the UI / reports).
    use_judge:     enable the LLM judge for grounding/relevance (heuristics if off
                   or unavailable).
    """
    settings = settings or get_settings()
    results: list[EvalResult] = []

    for ex in examples:
        req = _example_to_request(ex)
        try:
            resp = orchestrator.chat(req)
        except Exception as exc:  # one bad example must not sink the run
            log.warning("example %s: orchestrator.chat failed: %s", ex.id, exc)
            results.append(_failed_result(ex, f"orchestrator error: {type(exc).__name__}"))
            continue
        try:
            results.append(evaluate_example(ex, resp, settings=settings, use_judge=use_judge))
        except Exception as exc:  # scoring should be pure, but stay defensive
            log.warning("example %s: scoring failed: %s", ex.id, exc)
            results.append(_failed_result(ex, f"scoring error: {type(exc).__name__}"))

    return _aggregate(dataset, results)


def _aggregate(dataset: str, results: list[EvalResult]) -> EvalRunSummary:
    """Build per-metric averages + overall pass rate from per-example results.

    ``metric_averages`` includes one entry per :class:`EvalMetric` plus an
    ``overall`` key. ``pass_rate`` is the fraction of examples whose every metric
    passed its threshold — a strict, single-number health signal.
    """
    n = len(results)
    metric_sums: dict[str, float] = {m.value: 0.0 for m in EvalMetric}
    metric_counts: dict[str, int] = {m.value: 0 for m in EvalMetric}
    overall_sum = 0.0
    fully_passed = 0

    for res in results:
        overall_sum += res.overall
        all_pass = bool(res.scores)
        for s in res.scores:
            metric_sums[s.metric.value] += s.score
            metric_counts[s.metric.value] += 1
            if not s.passed:
                all_pass = False
        if all_pass:
            fully_passed += 1

    metric_averages: dict[str, float] = {}
    for m in EvalMetric:
        c = metric_counts[m.value]
        metric_averages[m.value] = round(metric_sums[m.value] / c, 4) if c else 0.0
    metric_averages["overall"] = round(overall_sum / n, 4) if n else 0.0

    pass_rate = round(fully_passed / n, 4) if n else 0.0

    return EvalRunSummary(
        dataset=dataset,
        n=n,
        metric_averages=metric_averages,
        pass_rate=pass_rate,
        results=results,
    )


__all__ = ["run_dataset", "evaluate_example", "_example_to_request"]
