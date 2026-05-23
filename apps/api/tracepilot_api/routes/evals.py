"""Eval routes: list recent online results, and run the offline dataset.

GET ``/evals`` derives recent ``EvalResult`` objects from persisted traces (the
scores online evaluation pushed onto each trace) and computes a per-metric
summary. POST ``/evals/run`` executes ``tracepilot_evals.run_dataset`` against the
default dataset using the live orchestrator, returning an ``EvalRunSummary``.

Both endpoints degrade gracefully: no telemetry -> empty recents; eval package
unavailable -> a clear 503.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import EvalResult, EvalRunSummary, EvalScore
from tracepilot_shared.models.evals import EvalMetric
from tracepilot_shared.telemetry import TraceRecord, list_traces

from ..core.deps import get_orchestrator
from ..core.errors import ApiError

router = APIRouter(tags=["evals"])
log = get_logger("api.evals")

_RECENT_LIMIT = 50


class EvalsOverview(BaseModel):
    """Recent online eval results plus an aggregate metric summary."""

    recent: list[EvalResult]
    summary: dict[str, Any]


class EvalRunRequest(BaseModel):
    """Body for POST /evals/run. ``dataset`` selects a named dataset (default)."""

    dataset: str | None = None


@router.get("/evals", response_model=EvalsOverview)
def get_evals() -> EvalsOverview:
    """Return recent eval results (from trace scores) and a metric summary."""
    try:
        records = list_traces(limit=_RECENT_LIMIT)
    except Exception as exc:  # pragma: no cover
        log.warning("list_traces failed in /evals: %s", exc)
        records = []

    recent = [r for r in (_trace_to_eval(t) for t in records) if r is not None]
    summary = _summarize(recent)
    return EvalsOverview(recent=recent, summary=summary)


@router.post("/evals/run", response_model=EvalRunSummary)
def run_evals(
    body: EvalRunRequest | None = None,
    orchestrator: Any = Depends(get_orchestrator),
) -> EvalRunSummary:
    """Run the offline eval dataset through the orchestrator."""
    try:
        from tracepilot_evals import load_default_dataset, run_dataset  # type: ignore
    except Exception as exc:  # evals package not installed/ready
        raise ApiError.unavailable(f"evaluation package unavailable: {exc}")

    try:
        examples = load_default_dataset()
    except Exception as exc:
        raise ApiError.bad_request(f"failed to load dataset: {exc}")

    dataset_name = (body.dataset if body else None) or "default"
    try:
        summary = run_dataset(examples, orchestrator)
    except Exception as exc:  # pragma: no cover - long-running, guarded
        log.exception("run_dataset failed")
        raise ApiError.unavailable(f"eval run failed: {exc}")

    # Stamp the requested dataset name if the package left it blank.
    if not getattr(summary, "dataset", ""):
        summary.dataset = dataset_name
    return summary


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _trace_to_eval(trace: TraceRecord) -> EvalResult | None:
    """Build an ``EvalResult`` from the scores stored on a trace, if any."""
    if not trace.scores:
        return None
    scores: list[EvalScore] = []
    for name, value in trace.scores.items():
        metric = _coerce_metric(name)
        if metric is None:
            continue
        v = max(0.0, min(1.0, float(value)))
        scores.append(EvalScore(metric=metric, score=v, passed=v >= 0.6))
    if not scores:
        return None
    overall = mean(s.score for s in scores)
    return EvalResult(
        trace_id=trace.id,
        workflow=trace.workflow,
        scores=scores,
        overall=round(overall, 4),
    )


def _coerce_metric(name: str) -> EvalMetric | None:
    try:
        return EvalMetric(name)
    except ValueError:
        return None


def _summarize(results: list[EvalResult]) -> dict[str, Any]:
    """Aggregate per-metric averages and an overall pass rate across results."""
    if not results:
        return {"n": 0, "metric_averages": {}, "pass_rate": 0.0, "overall": 0.0}

    buckets: dict[str, list[float]] = {}
    passes = 0
    for r in results:
        for s in r.scores:
            buckets.setdefault(s.metric.value, []).append(s.score)
        if r.scores and all(s.passed for s in r.scores):
            passes += 1

    metric_averages = {k: round(mean(v), 4) for k, v in buckets.items()}
    overall = round(mean(r.overall for r in results), 4)
    return {
        "n": len(results),
        "metric_averages": metric_averages,
        "pass_rate": round(passes / len(results), 4),
        "overall": overall,
    }
