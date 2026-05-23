"""Workspace routes: create, list, get."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from tracepilot_shared.models import Workspace, WorkspaceCreate

from ..core.deps import get_store
from ..core.store import MetadataStore
from ..services.workspace_service import WorkspaceService

router = APIRouter(tags=["workspaces"])


def _service(store: MetadataStore) -> WorkspaceService:
    return WorkspaceService(store)


@router.post("/workspaces", response_model=Workspace)
def create_workspace(
    body: WorkspaceCreate,
    store: MetadataStore = Depends(get_store),
) -> Workspace:
    """Create a workspace from a name (+ optional description)."""
    return _service(store).create(body)


@router.get("/workspaces", response_model=list[Workspace])
def list_workspaces(store: MetadataStore = Depends(get_store)) -> list[Workspace]:
    """List all workspaces, newest first."""
    return _service(store).list()


@router.get("/workspaces/{workspace_id}", response_model=Workspace)
def get_workspace(
    workspace_id: str,
    store: MetadataStore = Depends(get_store),
) -> Workspace:
    """Fetch a single workspace by id."""
    return _service(store).get(workspace_id)
