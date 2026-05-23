"""Chat route: grounded Q&A / investigation through the agent orchestrator.

Flow (per ``docs/INTERNAL_CONTRACTS.md``):

1. Run ``orchestrator.chat(req)`` — the orchestrator owns its own ``Tracer`` and
   returns a ``ChatResponse`` with ``trace_id`` populated.
2. Best-effort online evaluation via ``tracepilot_evals.evaluate_chat`` and push
   the scores onto the same trace. A missing/raising eval package never fails the
   request; it only adds a warning.
3. Return the (possibly enriched) ``ChatResponse``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import ChatRequest, ChatResponse

from ..core.deps import get_orchestrator

router = APIRouter(tags=["chat"])
log = get_logger("api.chat")


@router.post("/chat/query", response_model=ChatResponse)
def chat_query(
    body: ChatRequest,
    orchestrator: Any = Depends(get_orchestrator),
) -> ChatResponse:
    """Answer a grounded question and attach best-effort online eval scores."""
    resp: ChatResponse = orchestrator.chat(body)
    _run_online_eval(body, resp)
    return resp


def _run_online_eval(req: ChatRequest, resp: ChatResponse) -> None:
    """Score the answer and push to its trace. Never raises to the caller."""
    try:
        from tracepilot_evals import evaluate_chat  # type: ignore
    except Exception:  # evals package not installed/ready
        return
    try:
        result = evaluate_chat(req, resp)
        _push_scores(resp.trace_id, result)
    except Exception as exc:  # pragma: no cover - eval is advisory
        log.debug("online eval failed: %s", exc)
        resp.warnings.append("online evaluation skipped")


def _push_scores(trace_id: str | None, result: Any) -> None:
    """Mirror eval scores to the trace's scores dict (Langfuse + Redis copy)."""
    if not trace_id or result is None:
        return
    try:
        from tracepilot_shared.telemetry import get_langfuse, load_trace

        scores = {s.metric.value: s.score for s in getattr(result, "scores", [])}
        if not scores:
            return
        lf = get_langfuse()
        if lf is not None:
            for name, value in scores.items():
                try:
                    lf.score(trace_id=trace_id, name=name, value=float(value))
                except Exception:
                    pass
            try:
                lf.flush()
            except Exception:
                pass
        # Also fold into our own persisted trace record so /traces shows them.
        record = load_trace(trace_id)
        if record is not None:
            record.scores.update({k: round(float(v), 4) for k, v in scores.items()})
            _persist_trace(record)
    except Exception as exc:  # pragma: no cover
        log.debug("score push failed: %s", exc)


def _persist_trace(record: Any) -> None:
    """Re-persist a trace record to Redis (best-effort)."""
    try:
        import redis  # type: ignore

        from tracepilot_shared.config import get_settings

        client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        client.set(
            f"tracepilot:trace:{record.id}",
            record.model_dump_json(),
            ex=60 * 60 * 24 * 7,
        )
    except Exception:  # pragma: no cover
        pass
