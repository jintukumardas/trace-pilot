"""FastAPI dependency providers.

These read the singletons that the lifespan handler built and stowed on
``app.state`` (see :mod:`tracepilot_api.main`). Keeping them here means routes
declare exactly the collaborator they need via ``Depends`` and stay decoupled
from how those collaborators are constructed.

Some collaborators are optional at runtime (the agent graph or the vector store
may be unbuilt if a heavy dependency is missing). The providers surface that as a
clean ``ServiceUnavailable`` ``ApiError`` rather than an ``AttributeError``.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from tracepilot_shared.config import Settings, get_settings

from .errors import ApiError
from .runtime import ApiRepoLocator
from .store import MetadataStore


def _state(request: Request, attr: str) -> Any:
    return getattr(request.app.state, attr, None)


def get_store(request: Request) -> MetadataStore:
    """Return the process-wide metadata store."""
    store = _state(request, "store")
    if store is None:  # pragma: no cover - lifespan always sets this
        raise ApiError.unavailable("metadata store is not initialized")
    return store


def get_settings_dep() -> Settings:
    """Return the cached settings singleton (DI-friendly wrapper)."""
    return get_settings()


def get_retriever(request: Request) -> Any:
    """Return the retrieval engine, or 503 if it could not be built."""
    retriever = _state(request, "retriever")
    if retriever is None:
        raise ApiError.unavailable("retrieval engine is unavailable (check Qdrant / embeddings)")
    return retriever


def get_ingestor(request: Request) -> Any:
    """Return the ingestion engine, or 503 if it could not be built."""
    ingestor = _state(request, "ingestor")
    if ingestor is None:
        raise ApiError.unavailable("ingestion engine is unavailable (check Qdrant / embeddings)")
    return ingestor


def get_orchestrator(request: Request) -> Any:
    """Return the agent orchestrator, or 503 if the agent package is unavailable."""
    orch = _state(request, "orchestrator")
    if orch is None:
        raise ApiError.unavailable("agent orchestrator is unavailable (tracepilot_agent not ready)")
    return orch


def get_repo_locator(request: Request) -> ApiRepoLocator:
    """Return the repo locator used to map repo ids to on-disk paths."""
    locator = _state(request, "repo_locator")
    if locator is None:  # pragma: no cover - lifespan always sets this
        raise ApiError.unavailable("repo locator is not initialized")
    return locator
