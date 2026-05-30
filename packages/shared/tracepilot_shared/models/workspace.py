"""Workspace and repository domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .common import JobStatus, RepoStatus, TimestampedModel, utcnow


class WorkspaceCreate(BaseModel):
    """Request body for creating a workspace."""

    name: str = Field(..., min_length=1, max_length=120, description="Human-readable workspace name")
    description: str | None = Field(default=None, max_length=1000)


class Workspace(TimestampedModel):
    """A logical grouping of repositories owned by a team."""

    id: str
    name: str
    slug: str
    description: str | None = None
    repository_count: int = 0


class RepositoryConnectRequest(BaseModel):
    """Connect a repo by local path or git URL. Exactly one of the two is required."""

    workspace_id: str
    name: str | None = Field(default=None, description="Defaults to the repo directory name")
    local_path: str | None = Field(default=None, description="Absolute path on the host/mount")
    git_url: str | None = Field(default=None, description="Clonable git URL (https or ssh)")
    branch: str = Field(default="main")


class RepositoryStats(BaseModel):
    """Aggregate counters produced by the last indexing run."""

    num_files: int = 0
    num_chunks: int = 0
    num_skipped: int = 0
    languages: dict[str, int] = Field(default_factory=dict)
    bytes_indexed: int = 0


class Repository(TimestampedModel):
    """A connected repository within a workspace."""

    id: str
    workspace_id: str
    name: str
    local_path: str | None = None
    git_url: str | None = None
    branch: str = "main"
    status: RepoStatus = RepoStatus.REGISTERED
    head_commit: str | None = None
    last_indexed_at: datetime | None = None
    stats: RepositoryStats = Field(default_factory=RepositoryStats)
    error: str | None = None


class IndexRequest(BaseModel):
    """Trigger (re)indexing of a repository."""

    incremental: bool = Field(default=True, description="Only re-embed changed files when possible")
    paths: list[str] | None = Field(default=None, description="Restrict indexing to these path prefixes")


class IndexJob(BaseModel):
    """Async indexing job tracked in Redis + metadata store."""

    id: str
    repository_id: str
    status: JobStatus = JobStatus.PENDING
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = ""
    stats: RepositoryStats = Field(default_factory=RepositoryStats)
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
