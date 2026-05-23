"""SQLite-backed metadata store for workspaces, repositories and index jobs.

The store is intentionally small and dependency-free (stdlib ``sqlite3`` only).
Rich/nested attributes (stats, languages, ...) are persisted as JSON columns so
the table shape never has to chase the Pydantic models. Every public method
accepts and returns the canonical ``tracepilot_shared`` models, giving the
service layer a typed boundary it can rely on.

Thread safety: the connection is created with ``check_same_thread=False`` and
every read/write is guarded by a single re-entrant lock. The indexing service
runs in a background thread and writes job progress here, so this matters.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from tracepilot_shared.ids import JOB, REPOSITORY, WORKSPACE, new_id
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    IndexJob,
    JobStatus,
    Repository,
    RepositoryStats,
    RepoStatus,
    Workspace,
)
from tracepilot_shared.models.common import utcnow

log = get_logger("api.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repositories (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    name            TEXT NOT NULL,
    local_path      TEXT,
    git_url         TEXT,
    branch          TEXT NOT NULL DEFAULT 'main',
    status          TEXT NOT NULL DEFAULT 'registered',
    head_commit     TEXT,
    last_indexed_at TEXT,
    stats           TEXT NOT NULL DEFAULT '{}',
    error           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    progress      REAL NOT NULL DEFAULT 0.0,
    message       TEXT NOT NULL DEFAULT '',
    stats         TEXT NOT NULL DEFAULT '{}',
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repo_workspace ON repositories(workspace_id);
CREATE INDEX IF NOT EXISTS idx_job_repository ON jobs(repository_id);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - defensive
        return None


class MetadataStore:
    """Thread-safe SQLite store returning shared Pydantic models."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()
        log.info("metadata store ready at %s", self._path)

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------------ #
    # Workspaces
    # ------------------------------------------------------------------ #
    def create_workspace(self, name: str, slug: str, description: str | None = None) -> Workspace:
        """Insert a workspace and return the hydrated model."""
        ws = Workspace(id=new_id(WORKSPACE), name=name, slug=slug, description=description)
        with self._lock:
            self._conn.execute(
                "INSERT INTO workspaces (id, name, slug, description, created_at) VALUES (?,?,?,?,?)",
                (ws.id, ws.name, ws.slug, ws.description, ws.created_at.isoformat()),
            )
            self._conn.commit()
        return ws

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
            count = self._repo_count(workspace_id) if row else 0
        return self._row_to_workspace(row, count) if row else None

    def list_workspaces(self) -> list[Workspace]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM workspaces ORDER BY created_at DESC").fetchall()
            return [self._row_to_workspace(r, self._repo_count(r["id"])) for r in rows]

    def _repo_count(self, workspace_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM repositories WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_workspace(row: sqlite3.Row, repo_count: int) -> Workspace:
        return Workspace(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            description=row["description"],
            repository_count=repo_count,
            created_at=_dt(row["created_at"]) or utcnow(),
        )

    # ------------------------------------------------------------------ #
    # Repositories
    # ------------------------------------------------------------------ #
    def create_repository(
        self,
        workspace_id: str,
        name: str,
        *,
        local_path: str | None = None,
        git_url: str | None = None,
        branch: str = "main",
        status: RepoStatus = RepoStatus.REGISTERED,
        head_commit: str | None = None,
    ) -> Repository:
        """Insert a repository row and return the hydrated model."""
        repo = Repository(
            id=new_id(REPOSITORY),
            workspace_id=workspace_id,
            name=name,
            local_path=local_path,
            git_url=git_url,
            branch=branch,
            status=status,
            head_commit=head_commit,
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO repositories
                   (id, workspace_id, name, local_path, git_url, branch, status,
                    head_commit, last_indexed_at, stats, error, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    repo.id,
                    repo.workspace_id,
                    repo.name,
                    repo.local_path,
                    repo.git_url,
                    repo.branch,
                    str(repo.status),
                    repo.head_commit,
                    _iso(repo.last_indexed_at),
                    repo.stats.model_dump_json(),
                    repo.error,
                    repo.created_at.isoformat(),
                ),
            )
            self._conn.commit()
        return repo

    def get_repository(self, repository_id: str) -> Repository | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM repositories WHERE id = ?", (repository_id,)).fetchone()
        return self._row_to_repository(row) if row else None

    def list_repositories(self, workspace_id: str) -> list[Repository]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM repositories WHERE workspace_id = ? ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
        return [self._row_to_repository(r) for r in rows]

    def update_repository(self, repo: Repository) -> Repository:
        """Persist the full repository model (upsert by id)."""
        with self._lock:
            self._conn.execute(
                """UPDATE repositories SET
                       workspace_id=?, name=?, local_path=?, git_url=?, branch=?, status=?,
                       head_commit=?, last_indexed_at=?, stats=?, error=?
                   WHERE id=?""",
                (
                    repo.workspace_id,
                    repo.name,
                    repo.local_path,
                    repo.git_url,
                    repo.branch,
                    str(repo.status),
                    repo.head_commit,
                    _iso(repo.last_indexed_at),
                    repo.stats.model_dump_json(),
                    repo.error,
                    repo.id,
                ),
            )
            self._conn.commit()
        return repo

    @staticmethod
    def _row_to_repository(row: sqlite3.Row) -> Repository:
        return Repository(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            local_path=row["local_path"],
            git_url=row["git_url"],
            branch=row["branch"] or "main",
            status=RepoStatus(row["status"]),
            head_commit=row["head_commit"],
            last_indexed_at=_dt(row["last_indexed_at"]),
            stats=_load_stats(row["stats"]),
            error=row["error"],
            created_at=_dt(row["created_at"]) or utcnow(),
        )

    # ------------------------------------------------------------------ #
    # Jobs
    # ------------------------------------------------------------------ #
    def create_job(self, repository_id: str, status: JobStatus = JobStatus.PENDING) -> IndexJob:
        """Insert a pending index job and return the hydrated model."""
        job = IndexJob(id=new_id(JOB), repository_id=repository_id, status=status)
        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs
                   (id, repository_id, status, progress, message, stats, error, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    job.id,
                    job.repository_id,
                    str(job.status),
                    job.progress,
                    job.message,
                    job.stats.model_dump_json(),
                    job.error,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )
            self._conn.commit()
        return job

    def get_job(self, job_id: str) -> IndexJob | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def update_job(self, job: IndexJob) -> IndexJob:
        """Persist the full job model, stamping ``updated_at``."""
        job.updated_at = utcnow()
        with self._lock:
            self._conn.execute(
                """UPDATE jobs SET
                       repository_id=?, status=?, progress=?, message=?, stats=?, error=?, updated_at=?
                   WHERE id=?""",
                (
                    job.repository_id,
                    str(job.status),
                    job.progress,
                    job.message,
                    job.stats.model_dump_json(),
                    job.error,
                    job.updated_at.isoformat(),
                    job.id,
                ),
            )
            self._conn.commit()
        return job

    def list_jobs(self, repository_id: str) -> list[IndexJob]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE repository_id = ? ORDER BY created_at DESC",
                (repository_id,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def latest_job(self, repository_id: str) -> IndexJob | None:
        """Return the most recently created job for a repository, if any."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE repository_id = ? ORDER BY created_at DESC LIMIT 1",
                (repository_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> IndexJob:
        return IndexJob(
            id=row["id"],
            repository_id=row["repository_id"],
            status=JobStatus(row["status"]),
            progress=float(row["progress"]),
            message=row["message"] or "",
            stats=_load_stats(row["stats"]),
            error=row["error"],
            created_at=_dt(row["created_at"]) or utcnow(),
            updated_at=_dt(row["updated_at"]) or utcnow(),
        )


def _load_stats(raw: Any) -> RepositoryStats:
    """Hydrate a ``RepositoryStats`` from a JSON column, fail-soft to defaults."""
    if not raw:
        return RepositoryStats()
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        return RepositoryStats.model_validate(data)
    except Exception:  # pragma: no cover - defensive
        return RepositoryStats()
