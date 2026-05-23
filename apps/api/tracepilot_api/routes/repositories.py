"""Repository routes: connect, list, get, index, status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tracepilot_shared.config import Settings
from tracepilot_shared.models import (
    IndexJob,
    IndexRequest,
    Repository,
    RepositoryConnectRequest,
)

from ..core.deps import get_ingestor, get_settings_dep, get_store
from ..core.store import MetadataStore
from ..services.indexing_service import IndexingService
from ..services.repository_service import RepositoryService

router = APIRouter(tags=["repositories"])


class RepositoryStatusResponse(BaseModel):
    """Combined repository + latest-job snapshot for the ingestion UI."""

    repository: Repository
    job: IndexJob | None = None


@router.post("/repositories/connect", response_model=Repository)
def connect_repository(
    body: RepositoryConnectRequest,
    store: MetadataStore = Depends(get_store),
    settings: Settings = Depends(get_settings_dep),
) -> Repository:
    """Connect a repository by local path or git URL (status=registered)."""
    return RepositoryService(store, settings).connect(body)


@router.get("/workspaces/{workspace_id}/repositories", response_model=list[Repository])
def list_repositories(
    workspace_id: str,
    store: MetadataStore = Depends(get_store),
    settings: Settings = Depends(get_settings_dep),
) -> list[Repository]:
    """List repositories in a workspace."""
    return RepositoryService(store, settings).list(workspace_id)


@router.get("/repositories/{repository_id}", response_model=Repository)
def get_repository(
    repository_id: str,
    store: MetadataStore = Depends(get_store),
    settings: Settings = Depends(get_settings_dep),
) -> Repository:
    """Fetch a single repository by id."""
    return RepositoryService(store, settings).get(repository_id)


@router.post("/repositories/{repository_id}/index", response_model=IndexJob)
def index_repository(
    repository_id: str,
    body: IndexRequest,
    store: MetadataStore = Depends(get_store),
    ingestor: Any = Depends(get_ingestor),
    settings: Settings = Depends(get_settings_dep),
) -> IndexJob:
    """Kick off (re)indexing in the background; returns the pending job."""
    service = IndexingService(store, ingestor, settings)
    return service.start_index(repository_id, body)


@router.get("/repositories/{repository_id}/status", response_model=RepositoryStatusResponse)
def repository_status(
    repository_id: str,
    store: MetadataStore = Depends(get_store),
    settings: Settings = Depends(get_settings_dep),
) -> RepositoryStatusResponse:
    """Return the repository and its latest index job (job may be null)."""
    # Reading status does not need the ingestor, so pass ``None``.
    service = IndexingService(store, ingestor=None, settings=settings)
    repo, job = service.get_status(repository_id)
    return RepositoryStatusResponse(repository=repo, job=job)
