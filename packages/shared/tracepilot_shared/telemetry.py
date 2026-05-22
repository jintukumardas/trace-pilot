"""Observability: a Redis-backed trace recorder that also mirrors to Langfuse.

Design notes
------------
We keep our *own* lightweight trace tree (persisted to Redis) so the diagnostics
panel in the UI works even when Langfuse is not configured. When Langfuse *is*
configured we additionally mirror spans/scores to it (best-effort; failures never
break a request). This is a real integration, not a fake one — the Langfuse SDK
calls are guarded only so a flaky telemetry backend can't take down the API.

Pinned to the Langfuse v2 SDK/server (simple self-host: only needs Postgres).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, Field

from .config import get_settings
from .ids import TRACE, new_id
from .logging import get_logger
from .models.common import utcnow

log = get_logger("telemetry")

_TRACE_INDEX_KEY = "tracepilot:traces"  # Redis list of recent trace ids
_TRACE_KEY = "tracepilot:trace:{id}"
_MAX_TRACES = 500


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


# --------------------------------------------------------------------------- #
# Data model for our own trace tree
# --------------------------------------------------------------------------- #
class SpanRecord(BaseModel):
    id: str
    name: str
    type: str = "span"  # span | generation | tool | retrieval
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    start_ms: float = 0.0
    end_ms: float = 0.0
    status: str = "ok"
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        return max(0.0, self.end_ms - self.start_ms)


class TraceRecord(BaseModel):
    id: str
    name: str
    workflow: str = "chat"
    status: str = "ok"
    input: Any = None
    output: Any = None
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    latency_ms: float = 0.0
    tags: list[str] = Field(default_factory=list)
    spans: list[SpanRecord] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Redis + Langfuse backends (both optional, both guarded)
# --------------------------------------------------------------------------- #
_redis_client = None
_redis_init = False


def _redis():
    global _redis_client, _redis_init
    if _redis_init:
        return _redis_client
    _redis_init = True
    try:
        import redis  # type: ignore

        _redis_client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        _redis_client.ping()
    except Exception as exc:  # pragma: no cover - infra optional
        log.warning("Redis unavailable for trace storage: %s", exc)
        _redis_client = None
    return _redis_client


_langfuse_client = None
_langfuse_init = False


def get_langfuse():
    """Return a cached Langfuse client, or ``None`` if disabled/unconfigured."""
    global _langfuse_client, _langfuse_init
    if _langfuse_init:
        return _langfuse_client
    _langfuse_init = True
    s = get_settings()
    if not (s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse  # type: ignore

        _langfuse_client = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host,
        )
    except Exception as exc:  # pragma: no cover
        log.warning("Langfuse init failed, telemetry will be Redis-only: %s", exc)
        _langfuse_client = None
    return _langfuse_client


def _persist(record: TraceRecord) -> None:
    r = _redis()
    if not r:
        return
    try:
        key = _TRACE_KEY.format(id=record.id)
        r.set(key, record.model_dump_json(), ex=60 * 60 * 24 * 7)
        r.lrem(_TRACE_INDEX_KEY, 0, record.id)
        r.lpush(_TRACE_INDEX_KEY, record.id)
        r.ltrim(_TRACE_INDEX_KEY, 0, _MAX_TRACES - 1)
    except Exception as exc:  # pragma: no cover
        log.debug("trace persist failed: %s", exc)


# --------------------------------------------------------------------------- #
# Public recorder API
# --------------------------------------------------------------------------- #
class SpanHandle:
    """Mutable handle returned by ``Tracer.span``; update output/metadata in-place."""

    def __init__(self, record: SpanRecord, lf_span: Any = None):
        self._record = record
        self._lf_span = lf_span

    def update(self, *, output: Any = None, metadata: dict[str, Any] | None = None) -> None:
        if output is not None:
            self._record.output = output
        if metadata:
            self._record.metadata.update(metadata)

    def error(self, message: str) -> None:
        self._record.status = "error"
        self._record.error = message


class Tracer:
    """A single end-to-end trace. Create one per API request."""

    def __init__(
        self,
        name: str,
        workflow: str = "chat",
        *,
        input: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.record = TraceRecord(
            id=new_id(TRACE),
            name=name,
            workflow=workflow,
            input=_safe(input),
            tags=tags or [workflow],
            metadata=metadata or {},
        )
        self._t0 = _now_ms()
        self._lf = get_langfuse()
        self._lf_trace = None
        if self._lf:
            try:
                self._lf_trace = self._lf.trace(
                    id=self.record.id,
                    name=name,
                    input=_safe(input),
                    tags=self.record.tags,
                    metadata=self.record.metadata,
                )
            except Exception as exc:  # pragma: no cover
                log.debug("langfuse trace create failed: %s", exc)

    @property
    def id(self) -> str:
        return self.record.id

    @contextmanager
    def span(
        self, name: str, *, type: str = "span", input: Any = None, metadata: dict[str, Any] | None = None
    ) -> Iterator[SpanHandle]:
        """Context manager recording one sub-step. Mirrors to Langfuse if enabled."""
        rec = SpanRecord(
            id=new_id("span"),
            name=name,
            type=type,
            input=_safe(input),
            metadata=metadata or {},
            start_ms=_now_ms(),
        )
        lf_span = None
        if self._lf_trace is not None:
            try:
                factory = self._lf_trace.generation if type == "generation" else self._lf_trace.span
                lf_span = factory(name=name, input=_safe(input), metadata=metadata or {})
            except Exception:  # pragma: no cover
                lf_span = None
        handle = SpanHandle(rec, lf_span)
        try:
            yield handle
        except Exception as exc:
            handle.error(repr(exc))
            raise
        finally:
            rec.end_ms = _now_ms()
            self.record.spans.append(rec)
            if lf_span is not None:
                try:
                    lf_span.end(
                        output=_safe(rec.output), level="ERROR" if rec.status == "error" else "DEFAULT"
                    )
                except Exception:  # pragma: no cover
                    pass

    def score(self, name: str, value: float, comment: str = "") -> None:
        """Attach an evaluation score to the trace (0..1)."""
        self.record.scores[name] = round(float(value), 4)
        if self._lf_trace is not None:
            try:
                self._lf_trace.score(name=name, value=float(value), comment=comment)
            except Exception:  # pragma: no cover
                pass

    def finish(self, *, output: Any = None, status: str = "ok") -> TraceRecord:
        self.record.output = _safe(output)
        self.record.status = status
        self.record.latency_ms = round(_now_ms() - self._t0, 2)
        _persist(self.record)
        if self._lf_trace is not None:
            try:
                self._lf_trace.update(output=_safe(output))
                self._lf.flush()  # type: ignore[union-attr]
            except Exception:  # pragma: no cover
                pass
        return self.record


def _safe(value: Any) -> Any:
    """Make a value JSON-serializable and bounded for telemetry payloads."""
    if value is None:
        return None
    try:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        json.dumps(value, default=str)
        return value
    except Exception:
        return str(value)[:4000]


def load_trace(trace_id: str) -> TraceRecord | None:
    r = _redis()
    if not r:
        return None
    raw = r.get(_TRACE_KEY.format(id=trace_id))
    return TraceRecord.model_validate_json(raw) if raw else None


def list_traces(limit: int = 50, workflow: str | None = None) -> list[TraceRecord]:
    r = _redis()
    if not r:
        return []
    ids = r.lrange(_TRACE_INDEX_KEY, 0, max(0, limit * 3))
    out: list[TraceRecord] = []
    for tid in ids:
        raw = r.get(_TRACE_KEY.format(id=tid))
        if not raw:
            continue
        rec = TraceRecord.model_validate_json(raw)
        if workflow and rec.workflow != workflow:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out
