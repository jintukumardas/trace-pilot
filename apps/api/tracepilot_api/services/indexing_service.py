"""Indexing service: run the Ingestor in a background thread and stream progress.

``start_index`` creates a ``pending`` ``IndexJob``, flips the repository to
``indexing`` and launches a daemon thread. The thread drives
``Ingestor.ingest(...)`` with a progress callback that writes every update into
the metadata store *and* mirrors live job state to Redis (best-effort) so the UI
can poll either source. On completion the repository becomes ``indexed`` (or
``error``) with refreshed ``stats``, ``head_commit`` and ``last_indexed_at``.

Redis is treated as a cache only: if it is unavailable the SQLite store remains
the source of truth and indexing proceeds unaffected.
"""

from __future__ import annotations

import threading
from typing import Any

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    IndexJob,
    IndexRequest,
    JobStatus,
    Repository,
    RepositoryStats,
    RepoStatus,
)
from tracepilot_shared.models.common import utcnow

from ..core.errors import ApiError
from ..core.store import MetadataStore

log = get_logger("api.indexing")

_JOB_KEY = "tracepilot:job:{id}"
_REPO_JOB_KEY = "tracepilot:repo:{id}:job"  # latest job id per repo
_JOB_TTL_S = 60 * 60 * 24  # 1 day


class IndexingService:
    """Launch and observe asynchronous repository indexing jobs."""

    def __init__(
        self,
        store: MetadataStore,
        ingestor: Any,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.ingestor = ingestor
        self.settings = settings or get_settings()
        self._redis = self._connect_redis()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def start_index(self, repository_id: str, request: IndexRequest) -> IndexJob:
        """Create a pending job and kick off indexing in a background thread."""
        repo = self.store.get_repository(repository_id)
        if repo is None:
            raise ApiError.not_found("repository", repository_id)
        if self.ingestor is None:
            raise ApiError.unavailable("ingestion engine is unavailable (check Qdrant / embeddings)")

        job = self.store.create_job(repository_id, status=JobStatus.PENDING)
        self._mirror_job(job)

        # Flip repo into 'indexing' immediately so the UI reflects the transition.
        repo.status = RepoStatus.INDEXING
        repo.error = None
        self.store.update_repository(repo)

        thread = threading.Thread(
            target=self._run,
            args=(repo, request, job.id),
            name=f"index-{job.id}",
            daemon=True,
        )
        thread.start()
        log.info("started index job %s for repo %s", job.id, repository_id)
        return job

    def get_status(self, repository_id: str) -> tuple[Repository, IndexJob | None]:
        """Return the repository and its latest job (preferring fresh Redis state)."""
        repo = self.store.get_repository(repository_id)
        if repo is None:
            raise ApiError.not_found("repository", repository_id)
        job = self._latest_job(repository_id)
        return repo, job

    # ------------------------------------------------------------------ #
    # Background worker
    # ------------------------------------------------------------------ #
    def _run(self, repo: Repository, request: IndexRequest, job_id: str) -> None:
        """Worker body: drive ingestion, stream progress, finalize repo state."""
        job = self.store.get_job(job_id)
        if job is None:  # pragma: no cover - defensive
            return
        job.status = JobStatus.RUNNING
        job.message = "starting indexing"
        self._save_job(job)

        def on_progress(fraction: float, message: str) -> None:
            current = self.store.get_job(job_id) or job
            current.progress = max(0.0, min(1.0, fraction))
            current.message = message
            current.status = JobStatus.RUNNING
            self._save_job(current)

        try:
            stats: RepositoryStats = self.ingestor.ingest(repo, request, progress=on_progress)
            self._finalize_success(repo, job_id, stats)
        except Exception as exc:  # pragma: no cover - top-level guard
            log.exception("index job %s failed", job_id)
            self._finalize_failure(repo, job_id, str(exc))

    def _finalize_success(self, repo: Repository, job_id: str, stats: RepositoryStats) -> None:
        # Commit the repository's terminal state *before* marking the job done so a
        # client that observes a terminal job always sees a terminal repo too.
        fresh = self.store.get_repository(repo.id) or repo
        fresh.status = RepoStatus.INDEXED
        fresh.stats = stats
        fresh.last_indexed_at = utcnow()
        fresh.error = None
        # Refresh head commit if the ingestor resolved a newer working tree.
        head = self._resolve_head(fresh)
        if head:
            fresh.head_commit = head
        self.store.update_repository(fresh)

        job = self.store.get_job(job_id)
        if job is not None:
            job.status = JobStatus.SUCCEEDED
            job.progress = 1.0
            job.stats = stats
            job.message = f"indexed {stats.num_files} files, {stats.num_chunks} chunks"
            job.error = None
            self._save_job(job)
        log.info("index job %s succeeded (%d chunks)", job_id, stats.num_chunks)

    def _finalize_failure(self, repo: Repository, job_id: str, message: str) -> None:
        fresh = self.store.get_repository(repo.id) or repo
        fresh.status = RepoStatus.ERROR
        fresh.error = message
        self.store.update_repository(fresh)

        job = self.store.get_job(job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.error = message
            job.message = "indexing failed"
            self._save_job(job)

    @staticmethod
    def _resolve_head(repo: Repository) -> str | None:
        from pathlib import Path

        if not repo.local_path or not Path(repo.local_path).exists():
            return None
        try:
            from git import Repo  # type: ignore

            return Repo(repo.local_path).head.commit.hexsha
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Persistence helpers (store + Redis mirror)
    # ------------------------------------------------------------------ #
    def _save_job(self, job: IndexJob) -> None:
        self.store.update_job(job)
        self._mirror_job(job)

    def _latest_job(self, repository_id: str) -> IndexJob | None:
        """Prefer the live Redis copy; fall back to the durable store."""
        redis_job = self._read_redis_job(repository_id)
        store_job = self.store.latest_job(repository_id)
        if redis_job is None:
            return store_job
        if store_job is None:
            return redis_job
        # Both exist: trust whichever was updated most recently.
        return redis_job if redis_job.updated_at >= store_job.updated_at else store_job

    # ------------------------------------------------------------------ #
    # Redis (optional cache)
    # ------------------------------------------------------------------ #
    def _connect_redis(self) -> Any:
        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(self.settings.redis_url, decode_responses=True)
            client.ping()
            return client
        except Exception as exc:  # pragma: no cover - infra optional
            log.warning("Redis unavailable for job mirroring: %s", exc)
            return None

    def _mirror_job(self, job: IndexJob) -> None:
        if self._redis is None:
            return
        try:
            self._redis.set(_JOB_KEY.format(id=job.id), job.model_dump_json(), ex=_JOB_TTL_S)
            self._redis.set(_REPO_JOB_KEY.format(id=job.repository_id), job.id, ex=_JOB_TTL_S)
        except Exception as exc:  # pragma: no cover
            log.debug("redis job mirror failed: %s", exc)

    def _read_redis_job(self, repository_id: str) -> IndexJob | None:
        if self._redis is None:
            return None
        try:
            job_id = self._redis.get(_REPO_JOB_KEY.format(id=repository_id))
            if not job_id:
                return None
            raw = self._redis.get(_JOB_KEY.format(id=job_id))
            return IndexJob.model_validate_json(raw) if raw else None
        except Exception as exc:  # pragma: no cover
            log.debug("redis job read failed: %s", exc)
            return None
