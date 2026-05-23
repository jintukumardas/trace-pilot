"""FastAPI application factory and ASGI entrypoint.

``create_app()`` builds the FastAPI app, wires CORS from settings, installs the
uniform error envelope, and mounts every router. A lifespan handler constructs
the long-lived singletons once at startup and stores them on ``app.state``:

    store        MetadataStore (SQLite)         — always built
    embedder     retrieval embedder             — best-effort
    qdrant_store retrieval vector store         — best-effort
    retriever    Retriever(store, embedder)     — best-effort
    ingestor     Ingestor(store, embedder)      — best-effort
    repo_locator ApiRepoLocator(store)          — always built
    orchestrator agent Orchestrator             — best-effort

Heavy collaborators (embeddings model, Qdrant, the agent graph) are built behind
guards so the API still serves health, metadata and trace routes when an optional
backend is missing — those routes degrade to a clean 503 instead of crashing the
process at import time.

``app = create_app()`` is exposed module-level for ``uvicorn
tracepilot_api.main:app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import configure_logging, get_logger

from .core.errors import install_exception_handlers
from .core.runtime import ApiRepoLocator
from .core.store import MetadataStore
from .routes import ALL_ROUTERS

log = get_logger("api.main")

API_TITLE = "TracePilot API"
API_VERSION = "0.1.0"


def _db_path(settings: Settings) -> str:
    """Resolve the SQLite path from ``database_url`` (sqlite:///...) or data_dir."""
    url = settings.database_url
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        return url[len("sqlite://") :]
    # Non-sqlite URL configured but we only support sqlite here: fall back.
    return str(Path(settings.data_dir) / "tracepilot.db")


def _ensure_dirs(settings: Settings) -> None:
    for d in (settings.data_dir, settings.workspaces_dir):
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - permissions
            log.warning("could not create directory %s: %s", d, exc)


def _build_retrieval(settings: Settings) -> tuple[Any, Any, Any, Any]:
    """Build embedder, qdrant store, retriever and ingestor (best-effort)."""
    embedder = qdrant_store = retriever = ingestor = None
    try:
        from tracepilot_retrieval import (
            Ingestor,
            Retriever,
            get_embedder,
            get_qdrant_store,
        )

        embedder = get_embedder(settings)
        qdrant_store = get_qdrant_store(settings)
        retriever = Retriever(qdrant_store, embedder, settings)
        ingestor = Ingestor(qdrant_store, embedder, settings)
        log.info("retrieval stack ready (embedder=%s)", getattr(embedder, "name", "?"))
    except Exception as exc:
        log.warning("retrieval stack unavailable, retrieval routes will 503: %s", exc)
    return embedder, qdrant_store, retriever, ingestor


def _build_orchestrator(retriever: Any, repo_locator: ApiRepoLocator, settings: Settings) -> Any:
    """Build the agent orchestrator (best-effort)."""
    if retriever is None:
        log.warning("orchestrator not built: retriever unavailable")
        return None
    try:
        from tracepilot_agent import Orchestrator  # type: ignore

        orch = Orchestrator(retriever, repo_locator, settings)
        log.info("agent orchestrator ready")
        return orch
    except Exception as exc:
        log.warning("agent orchestrator unavailable, chat/debug/review will 503: %s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Construct singletons on startup and dispose of them on shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    _ensure_dirs(settings)

    store = MetadataStore(_db_path(settings))
    repo_locator = ApiRepoLocator(store, settings)
    embedder, qdrant_store, retriever, ingestor = _build_retrieval(settings)
    orchestrator = _build_orchestrator(retriever, repo_locator, settings)

    app.state.settings = settings
    app.state.store = store
    app.state.repo_locator = repo_locator
    app.state.embedder = embedder
    app.state.qdrant_store = qdrant_store
    app.state.retriever = retriever
    app.state.ingestor = ingestor
    app.state.orchestrator = orchestrator

    log.info("%s %s started (env=%s)", API_TITLE, API_VERSION, settings.app_env)
    try:
        yield
    finally:
        try:
            store.close()
        except Exception:  # pragma: no cover
            pass
        log.info("%s shutting down", API_TITLE)


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    settings = get_settings()
    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan)

    origins = settings.cors_origin_list or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_exception_handlers(app)

    for router in ALL_ROUTERS:
        app.include_router(router)

    return app


app = create_app()
