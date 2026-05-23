"""Health check: liveness plus a probe of each backing service."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from tracepilot_shared.config import get_settings

router = APIRouter(tags=["health"])


def _probe_redis(settings: Any) -> str:
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return "ok"
    except Exception:
        return "down"


def _probe_qdrant(request: Request) -> str:
    store = getattr(request.app.state, "qdrant_store", None)
    if store is None:
        return "unavailable"
    try:
        store.count(None)
        return "ok"
    except Exception:
        return "down"


def _state_status(request: Request, attr: str) -> str:
    return "ok" if getattr(request.app.state, attr, None) is not None else "unavailable"


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Return overall status and a per-service breakdown.

    ``status`` is ``ok`` when the metadata store is live; individual subsystems
    report their own state so the dashboard can surface partial degradation.
    """
    settings = get_settings()
    services = {
        "store": _state_status(request, "store"),
        "retriever": _state_status(request, "retriever"),
        "ingestor": _state_status(request, "ingestor"),
        "orchestrator": _state_status(request, "orchestrator"),
        "qdrant": _probe_qdrant(request),
        "redis": _probe_redis(settings),
    }
    status = "ok" if services["store"] == "ok" else "degraded"
    return {"status": status, "services": services}
