"""Trace routes: list condensed trace summaries and fetch a full trace record.

Reads from the shared telemetry store (Redis-backed, with optional Langfuse
mirroring). When Redis is unavailable both endpoints degrade to empty / 404
rather than raising.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import TraceSummary
from tracepilot_shared.telemetry import (
    TraceRecord,
    list_traces,
    load_trace,
)

from ..core.errors import ApiError

router = APIRouter(tags=["traces"])
log = get_logger("api.traces")

_PREVIEW_CHARS = 280


@router.get("/traces", response_model=list[TraceSummary])
def get_traces(
    limit: int = Query(default=50, ge=1, le=500),
    workflow: str | None = Query(default=None),
) -> list[TraceSummary]:
    """Return recent traces as compact summaries (optionally filtered by workflow)."""
    try:
        records = list_traces(limit=limit, workflow=workflow)
    except Exception as exc:  # pragma: no cover - telemetry optional
        log.warning("list_traces failed: %s", exc)
        records = []
    return [_to_summary(r) for r in records]


@router.get("/traces/{trace_id}", response_model=TraceRecord)
def get_trace(trace_id: str) -> TraceRecord:
    """Return the full trace tree (spans, scores, metadata) for one trace."""
    try:
        record = load_trace(trace_id)
    except Exception as exc:  # pragma: no cover
        log.warning("load_trace failed: %s", exc)
        record = None
    if record is None:
        raise ApiError.not_found("trace", trace_id)
    return record


def _to_summary(record: TraceRecord) -> TraceSummary:
    """Map a full ``TraceRecord`` to the lighter ``TraceSummary`` UI model."""
    return TraceSummary(
        id=record.id,
        name=record.name,
        workflow=record.workflow,
        status=record.status,
        latency_ms=record.latency_ms,
        input_preview=_preview(record.input),
        output_preview=_preview(record.output),
        scores=dict(record.scores),
        metadata=dict(record.metadata),
    )


def _preview(value: object) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = text.strip().replace("\n", " ")
    return text[:_PREVIEW_CHARS]
