"""Runtime glue between the API metadata store and the agent graph.

``ApiRepoLocator`` implements the ``tracepilot_agent.runtime.RepoLocator``
protocol (structurally — see ``docs/INTERNAL_CONTRACTS.md``). The orchestrator
calls it to turn a ``repository_id`` into an absolute local working-tree path and
a human-readable name when it needs to run sandboxed tools (read_file, repo_search,
git_diff, ...).

Resolution mirrors the ingestor: prefer an existing ``local_path``; otherwise the
clone destination under ``settings.workspaces_dir/<repo_id>`` (where the ingestor
clones git URLs). Returns ``None`` when nothing is on disk so the agent can fall
back to retrieval-only reasoning instead of crashing.
"""

from __future__ import annotations

from pathlib import Path

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger

from .store import MetadataStore

log = get_logger("api.runtime")


class ApiRepoLocator:
    """Resolve repository ids to on-disk working trees for the agent's tools."""

    def __init__(self, store: MetadataStore, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings or get_settings()

    def resolve(self, repository_id: str) -> str | None:
        """Return the absolute local path of the repository, or ``None``."""
        repo = self.store.get_repository(repository_id)
        if repo is None:
            log.debug("repo_locator: unknown repository %s", repository_id)
            return None

        # 1. Explicit local mount/path wins.
        if repo.local_path:
            p = Path(repo.local_path)
            if p.exists():
                return str(p.resolve())

        # 2. Clone destination used by the ingestor for git URLs.
        clone_dest = Path(self.settings.workspaces_dir).resolve() / repo.id
        if clone_dest.exists():
            return str(clone_dest)

        log.debug("repo_locator: no working tree on disk for %s", repository_id)
        return None

    def name(self, repository_id: str) -> str:
        """Return a human-readable name for the repository (id as fallback)."""
        repo = self.store.get_repository(repository_id)
        return repo.name if repo is not None else repository_id
