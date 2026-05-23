"""Workspace service: create / list / get with name slugification."""

from __future__ import annotations

import re

from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import Workspace, WorkspaceCreate

from ..core.errors import ApiError
from ..core.store import MetadataStore

log = get_logger("api.workspace")

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase, ASCII, hyphen-joined slug (e.g. 'Platform Core' -> 'platform-core')."""
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    return slug or "workspace"


class WorkspaceService:
    """CRUD for workspaces; deliberately thin over :class:`MetadataStore`."""

    def __init__(self, store: MetadataStore) -> None:
        self.store = store

    def create(self, body: WorkspaceCreate) -> Workspace:
        """Create a workspace, deriving a unique slug from its name."""
        base = slugify(body.name)
        slug = self._unique_slug(base)
        ws = self.store.create_workspace(name=body.name, slug=slug, description=body.description)
        log.info("created workspace %s (%s)", ws.id, ws.slug)
        return ws

    def list(self) -> list[Workspace]:
        """Return all workspaces, newest first."""
        return self.store.list_workspaces()

    def get(self, workspace_id: str) -> Workspace:
        """Return a workspace or raise ``ApiError.not_found``."""
        ws = self.store.get_workspace(workspace_id)
        if ws is None:
            raise ApiError.not_found("workspace", workspace_id)
        return ws

    def _unique_slug(self, base: str) -> str:
        existing = {w.slug for w in self.store.list_workspaces()}
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"
