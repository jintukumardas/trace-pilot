"""Online evaluation — score a live chat turn and record the result.

``evaluate_chat`` is called by the API right after ``orchestrator.chat`` returns.
It is **best-effort and fail-soft**: scoring never raises into the request path,
and telemetry/persistence failures are swallowed (logged at debug). It does three
things:

1. Score the response with all metrics (LLM judge for grounding/relevance when
   enabled, heuristics otherwise) and build an :class:`EvalResult` whose
   ``overall`` is the mean of the metric scores.
2. If the response carries a ``trace_id``, push each metric to Langfuse via the
   shared telemetry layer (``get_langfuse().score(...)`` keyed to the trace), and
   mirror the scores onto our own persisted :class:`TraceRecord`.
3. Persist the :class:`EvalResult` (JSON) to the Redis list ``tracepilot:evals``
   so the ``GET /evals`` endpoint can show recent results + a rolling summary.
"""

from __future__ import annotations

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import ChatRequest, ChatResponse, EvalResult

from .judge import score_with_judge

log = get_logger("evals.online")

# Redis list the API's GET /evals reads from. Newest first (LPUSH), capped.
EVALS_KEY = "tracepilot:evals"
_MAX_EVALS = 500


def evaluate_chat(
    req: ChatRequest,
    resp: ChatResponse,
    *,
    settings: Settings | None = None,
    use_judge: bool = True,
) -> EvalResult:
    """Score a chat turn, push scores to the trace, persist for the dashboard.

    Returns the :class:`EvalResult` regardless of whether telemetry/persistence
    succeeded — the API uses the return value and treats side-effects as advisory.
    """
    settings = settings or get_settings()

    scores = score_with_judge(req, resp, settings=settings, use_judge=use_judge)
    overall = round(sum(s.score for s in scores) / len(scores), 4) if scores else 0.0

    result = EvalResult(
        trace_id=resp.trace_id,
        workflow=str(req.mode),
        scores=scores,
        overall=overall,
    )

    if resp.trace_id:
        _push_to_trace(resp.trace_id, result)
    _persist(result, settings)
    return result


# --------------------------------------------------------------------------- #
# Langfuse / trace mirroring
# --------------------------------------------------------------------------- #
def _push_to_trace(trace_id: str, result: EvalResult) -> None:
    """Attach each metric score to the existing trace (Langfuse + our TraceRecord).

    The agent graph already created and *finished* the trace by the time the API
    evaluates, so we cannot reuse the original :class:`Tracer`. Instead we score
    the trace directly by id: Langfuse scores reference a ``trace_id``, and we
    re-load + re-persist our own :class:`TraceRecord` so the diagnostics UI shows
    the eval scores next to the run.
    """
    # 1) Langfuse: score by trace id (best-effort).
    try:
        from tracepilot_shared.telemetry import get_langfuse

        lf = get_langfuse()
        if lf is not None:
            for s in result.scores:
                try:
                    lf.score(
                        trace_id=trace_id,
                        name=s.metric.value,
                        value=float(s.score),
                        comment=s.rationale or "",
                    )
                except Exception as exc:  # pragma: no cover - telemetry optional
                    log.debug("langfuse score push failed (%s): %s", s.metric.value, exc)
            try:
                lf.score(trace_id=trace_id, name="overall", value=float(result.overall))
            except Exception:  # pragma: no cover
                pass
            try:
                lf.flush()
            except Exception:  # pragma: no cover
                pass
    except Exception as exc:  # pragma: no cover - import/telemetry optional
        log.debug("langfuse unavailable for eval scores: %s", exc)

    # 2) Our own persisted TraceRecord: merge scores so /traces/{id} shows them.
    try:
        from tracepilot_shared.telemetry import (  # local import keeps module import light
            _TRACE_KEY,
            _redis,
            load_trace,
        )

        record = load_trace(trace_id)
        r = _redis()
        if record is not None and r is not None:
            for s in result.scores:
                record.scores[s.metric.value] = round(float(s.score), 4)
            record.scores["overall"] = float(result.overall)
            r.set(_TRACE_KEY.format(id=trace_id), record.model_dump_json(), ex=60 * 60 * 24 * 7)
    except Exception as exc:  # pragma: no cover
        log.debug("trace score merge failed: %s", exc)


# --------------------------------------------------------------------------- #
# Redis persistence for GET /evals
# --------------------------------------------------------------------------- #
def _persist(result: EvalResult, settings: Settings) -> None:
    """Append the eval result to the Redis list the dashboard reads (fail-soft)."""
    try:
        from tracepilot_shared.telemetry import _redis

        r = _redis()
        if r is None:
            return
        r.lpush(EVALS_KEY, result.model_dump_json())
        r.ltrim(EVALS_KEY, 0, _MAX_EVALS - 1)
    except Exception as exc:  # pragma: no cover - infra optional
        log.debug("eval persist failed: %s", exc)


def recent_evals(limit: int = 50, settings: Settings | None = None) -> list[EvalResult]:
    """Load recent persisted eval results (newest first). Helper for ``GET /evals``."""
    try:
        from tracepilot_shared.telemetry import _redis

        r = _redis()
        if r is None:
            return []
        raw = r.lrange(EVALS_KEY, 0, max(0, limit - 1))
    except Exception as exc:  # pragma: no cover
        log.debug("recent_evals load failed: %s", exc)
        return []

    out: list[EvalResult] = []
    for item in raw:
        try:
            out.append(EvalResult.model_validate_json(item))
        except Exception:
            continue
    return out


__all__ = ["evaluate_chat", "recent_evals", "EVALS_KEY"]
