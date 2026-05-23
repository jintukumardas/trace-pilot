"""Repository service: connect (local path or git clone), list and get.

``connect`` validates that exactly one source is provided, then either confirms a
local path exists or shallow-clones the git URL into
``settings.workspaces_dir/<repo_id>`` via GitPython. The repository is persisted
with ``status=registered`` and (when resolvable) its HEAD commit. Indexing is a
separate, explicit step (``/repositories/{id}/index``).

The clone is fail-soft: a network or auth failure leaves the repository
registered with an ``error`` set instead of bubbling a 500, so the UI can show
the problem and let the user retry.
"""

from __future__ import annotations

from pathlib import Path

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    Repository,
    RepositoryConnectRequest,
    RepoStatus,
)

from ..core.errors import ApiError
from ..core.store import MetadataStore

log = get_logger("api.repository")


class RepositoryService:
    """Connect and read repositories within a workspace."""

    def __init__(self, store: MetadataStore, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ #
    # Connect
    # ------------------------------------------------------------------ #
    def connect(self, body: RepositoryConnectRequest) -> Repository:
        """Register a repository from a local path or a clonable git URL."""
        if self.store.get_workspace(body.workspace_id) is None:
            raise ApiError.not_found("workspace", body.workspace_id)

        has_local = bool(body.local_path)
        has_git = bool(body.git_url)
        if has_local == has_git:
            raise ApiError.bad_request("provide exactly one of 'local_path' or 'git_url'")

        if has_local:
            return self._connect_local(body)
        return self._connect_git(body)

    def _connect_local(self, body: RepositoryConnectRequest) -> Repository:
        path = Path(body.local_path).expanduser()
        if not path.exists() or not path.is_dir():
            raise ApiError.bad_request(f"local_path does not exist or is not a directory: {body.local_path}")
        name = body.name or path.name
        repo = self.store.create_repository(
            workspace_id=body.workspace_id,
            name=name,
            local_path=str(path.resolve()),
            branch=body.branch,
            status=RepoStatus.REGISTERED,
            head_commit=self._head_commit(path),
        )
        log.info("connected local repo %s (%s)", repo.id, repo.local_path)
        return repo

    def _connect_git(self, body: RepositoryConnectRequest) -> Repository:
        name = body.name or self._name_from_url(body.git_url)
        # Persist first so we have a stable id for the clone destination.
        repo = self.store.create_repository(
            workspace_id=body.workspace_id,
            name=name,
            git_url=body.git_url,
            branch=body.branch,
            status=RepoStatus.REGISTERED,
        )
        dest = Path(self.settings.workspaces_dir).resolve() / repo.id
        try:
            self._clone(body.git_url, dest, body.branch)
            repo.local_path = str(dest)
            repo.head_commit = self._head_commit(dest)
            log.info("cloned repo %s from %s", repo.id, body.git_url)
        except Exception as exc:  # fail-soft: keep the registration, record the error
            log.warning("clone failed for %s: %s", body.git_url, exc)
            repo.error = f"clone failed: {exc}"
        return self.store.update_repository(repo)

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def list(self, workspace_id: str) -> list[Repository]:
        """Return repositories in a workspace (validates the workspace exists)."""
        if self.store.get_workspace(workspace_id) is None:
            raise ApiError.not_found("workspace", workspace_id)
        return self.store.list_repositories(workspace_id)

    def get(self, repository_id: str) -> Repository:
        """Return a repository or raise ``ApiError.not_found``."""
        repo = self.store.get_repository(repository_id)
        if repo is None:
            raise ApiError.not_found("repository", repository_id)
        return repo

    # ------------------------------------------------------------------ #
    # Git helpers (GitPython, guarded)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clone(git_url: str, dest: Path, branch: str) -> None:
        from git import Repo  # type: ignore

        dest.parent.mkdir(parents=True, exist_ok=True)
        if (dest / ".git").exists():
            # Already cloned: leave it for the ingestor's incremental pull.
            return
        try:
            Repo.clone_from(git_url, str(dest), branch=branch, depth=1, single_branch=True)
        except Exception:
            # Branch may not exist or shallow/single-branch unsupported: retry default.
            Repo.clone_from(git_url, str(dest), depth=1)

    @staticmethod
    def _head_commit(root: Path) -> str | None:
        try:
            from git import Repo  # type: ignore

            return Repo(str(root)).head.commit.hexsha
        except Exception:
            return None

    @staticmethod
    def _name_from_url(git_url: str) -> str:
        tail = git_url.rstrip("/").split("/")[-1]
        return tail[:-4] if tail.endswith(".git") else tail or "repository"
