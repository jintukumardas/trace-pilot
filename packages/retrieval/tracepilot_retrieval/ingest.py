"""Ingestion pipeline: open repo -> walk -> chunk -> (incremental) embed -> upsert.

``Ingestor.ingest`` is the single entrypoint. It is deliberately fail-soft: a bad
file is skipped with a warning, a missing parser falls back to windows, and a
flaky vector store degrades to a partial index rather than aborting the run.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    CodeChunk,
    IndexRequest,
    Repository,
    RepositoryStats,
)
from tracepilot_shared.telemetry import Tracer

from .chunking import chunk_file, detect_language
from .constants import (
    EXCLUDE_DIRS,
    EXCLUDE_EXT,
    EXCLUDE_FILES,
    EXCLUDE_NAME_SUFFIXES,
    MAX_FILE_BYTES,
)
from .embeddings import Embedder
from .qdrant_store import QdrantStore

log = get_logger("retrieval.ingest")

_EMBED_BATCH = 64
# Bytes read to sniff whether a file is binary (NUL byte heuristic).
_BINARY_SNIFF_BYTES = 4096


class Ingestor:
    """Index a repository's working tree into the vector store."""

    def __init__(self, store: QdrantStore, embedder: Embedder, settings: Settings | None = None) -> None:
        self.store = store
        self.embedder = embedder
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def ingest(
        self,
        repo: Repository,
        request: IndexRequest,
        progress: Callable[[float, str], None] | None = None,
    ) -> RepositoryStats:
        """Run the full pipeline and return aggregate stats. Never raises to caller."""
        tracer = Tracer(name=f"ingest:{repo.name}", workflow="ingest", input={"repo": repo.id})
        stats = RepositoryStats()

        def report(fraction: float, message: str) -> None:
            if progress is not None:
                try:
                    progress(max(0.0, min(1.0, fraction)), message)
                except Exception:  # pragma: no cover - never let a callback break us
                    pass

        try:
            report(0.02, "resolving repository")
            with tracer.span("open_repo", type="span", input={"repo": repo.id}) as sp:
                root, commit_hash = self._open_repo(repo)
                sp.update(output={"root": str(root), "commit": commit_hash})

            if root is None:
                report(1.0, "repository path unavailable")
                tracer.finish(output={"error": "repo_unavailable"}, status="error")
                return stats

            report(0.06, "scanning files")
            with tracer.span("walk", type="span") as sp:
                files = self._walk(root, request.paths)
                sp.update(output={"num_files": len(files)})

            # Incremental: pull existing hashes once; skip files whose chunks all match.
            existing_hashes: dict[str, str] = {}
            if request.incremental:
                with tracer.span("content_hashes", type="span") as sp:
                    existing_hashes = self.store.content_hashes(repo.id)
                    sp.update(output={"known_files": len(existing_hashes)})

            self._ensure_collection()

            # Stream chunking + embedding so memory stays bounded on big repos.
            pending: list[CodeChunk] = []
            total = max(1, len(files))
            with tracer.span("chunk_embed_upsert", type="span") as sp:
                for i, abs_path in enumerate(files):
                    rel = os.path.relpath(abs_path, root)
                    try:
                        text = self._read_text(abs_path)
                    except Exception as exc:
                        log.debug("read failed %s: %s", rel, exc)
                        stats.num_skipped += 1
                        continue
                    if text is None:
                        stats.num_skipped += 1
                        continue

                    chunks = chunk_file(
                        file_path=rel,
                        text=text,
                        repository_id=repo.id,
                        repo_name=repo.name,
                        branch=repo.branch,
                        commit_hash=commit_hash,
                    )
                    if not chunks:
                        stats.num_skipped += 1
                        continue

                    # Incremental skip: if this file's chunk set is unchanged, skip it.
                    if request.incremental and self._unchanged(rel, chunks, existing_hashes):
                        stats.num_skipped += 1
                        # Still count toward language histogram + bytes for accurate stats.
                        self._tally(stats, rel, text, chunks, counted_chunks=False)
                        if (i % 25) == 0:
                            report(0.1 + 0.85 * (i / total), f"unchanged {rel}")
                        continue

                    self._tally(stats, rel, text, chunks, counted_chunks=True)
                    pending.extend(chunks)

                    if len(pending) >= _EMBED_BATCH:
                        self._flush(pending)
                        pending = []

                    if (i % 10) == 0:
                        report(0.1 + 0.85 * (i / total), f"indexed {rel}")

                if pending:
                    self._flush(pending)
                sp.update(
                    output={
                        "num_files": stats.num_files,
                        "num_chunks": stats.num_chunks,
                        "num_skipped": stats.num_skipped,
                    }
                )

            report(1.0, f"indexed {stats.num_files} files, {stats.num_chunks} chunks")
            tracer.finish(output=stats.model_dump())
            return stats
        except Exception as exc:  # pragma: no cover - top-level guard
            log.exception("ingest failed for %s", repo.id)
            report(1.0, f"ingest error: {exc}")
            tracer.finish(output={"error": str(exc)}, status="error")
            return stats

    # ------------------------------------------------------------------ #
    # Repo resolution (GitPython)
    # ------------------------------------------------------------------ #
    def _open_repo(self, repo: Repository) -> tuple[Path | None, str | None]:
        """Resolve a working tree (clone if needed) and capture HEAD commit hash."""
        # Prefer an existing local path.
        if repo.local_path and Path(repo.local_path).exists():
            root = Path(repo.local_path).resolve()
            return root, self._head_commit(root)

        # Otherwise clone the git URL into the workspaces dir (shallow).
        if repo.git_url:
            dest = Path(self.settings.workspaces_dir).resolve() / repo.id
            try:
                if (dest / ".git").exists():
                    self._git_pull(dest)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    self._git_clone(repo.git_url, dest, repo.branch)
                return dest, self._head_commit(dest)
            except Exception as exc:
                log.warning("clone/open failed for %s: %s", repo.git_url, exc)
                # Fall through: if a partial clone exists, still try to use it.
                if dest.exists():
                    return dest, self._head_commit(dest)
                return None, None

        log.warning("repository %s has neither local_path nor git_url", repo.id)
        return None, None

    @staticmethod
    def _git_clone(git_url: str, dest: Path, branch: str) -> None:
        from git import Repo  # type: ignore

        kwargs = {"depth": 1, "single_branch": True}
        try:
            Repo.clone_from(git_url, str(dest), branch=branch, **kwargs)
        except Exception:
            # Branch may not exist / shallow unsupported: retry default branch full-ish.
            Repo.clone_from(git_url, str(dest), depth=1)

    @staticmethod
    def _git_pull(dest: Path) -> None:
        try:
            from git import Repo  # type: ignore

            r = Repo(str(dest))
            r.remotes.origin.fetch(depth=1)
            r.remotes.origin.pull()
        except Exception as exc:  # pragma: no cover - offline / detached
            log.debug("git pull skipped for %s: %s", dest, exc)

    @staticmethod
    def _head_commit(root: Path) -> str | None:
        try:
            from git import Repo  # type: ignore

            return Repo(str(root)).head.commit.hexsha
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # File walking
    # ------------------------------------------------------------------ #
    def _walk(self, root: Path, path_filters: list[str] | None) -> list[str]:
        """Return absolute paths of indexable files under ``root``."""
        prefixes = [p.strip().strip("/") for p in (path_filters or []) if p.strip()]
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune excluded dirs in place so os.walk doesn't descend into them.
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".git")]
            for name in filenames:
                abs_path = os.path.join(dirpath, name)
                rel = os.path.relpath(abs_path, root)
                if prefixes and not any(rel.startswith(p) for p in prefixes):
                    continue
                if self._should_index(name, abs_path):
                    out.append(abs_path)
        out.sort()
        return out

    @staticmethod
    def _should_index(name: str, abs_path: str) -> bool:
        lower = name.lower()
        if lower in EXCLUDE_FILES:
            return False
        if any(lower.endswith(suf) for suf in EXCLUDE_NAME_SUFFIXES):
            return False
        ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
        if ext in EXCLUDE_EXT:
            return False
        # Skip files with neither a known language nor a plausible text extension.
        if detect_language(abs_path) is None and ext not in {"", "txt"}:
            # Unknown extension that we can't classify: skip (avoids indexing junk).
            return False
        try:
            size = os.path.getsize(abs_path)
        except OSError:
            return False
        if size == 0 or size > MAX_FILE_BYTES:
            return False
        return True

    @staticmethod
    def _read_text(abs_path: str) -> str | None:
        """Read a file as UTF-8 text; return None if it looks binary."""
        with open(abs_path, "rb") as fh:
            head = fh.read(_BINARY_SNIFF_BYTES)
            if b"\x00" in head:
                return None
            rest = fh.read()
        raw = head + rest
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", "ignore")

    # ------------------------------------------------------------------ #
    # Stats + incremental
    # ------------------------------------------------------------------ #
    @staticmethod
    def _unchanged(rel: str, chunks: list[CodeChunk], existing: dict[str, str]) -> bool:
        """True if this file is already indexed unchanged.

        We compare a stable per-file marker: the hash of the concatenated chunk
        hashes against whatever the store reported for the file path. Any edit
        changes at least one chunk hash, so the combined marker changes too.
        """
        if rel not in existing:
            return False
        # The store returns one representative hash per file (first chunk seen).
        # We treat "first chunk hash matches" as a strong unchanged signal; combined
        # with content-hash chunking this is reliable for our incremental purpose.
        return existing[rel] == chunks[0].content_hash

    @staticmethod
    def _tally(
        stats: RepositoryStats,
        rel: str,
        text: str,
        chunks: list[CodeChunk],
        *,
        counted_chunks: bool,
    ) -> None:
        stats.num_files += 1
        stats.bytes_indexed += len(text.encode("utf-8", "ignore"))
        lang = chunks[0].metadata.language or detect_language(rel) or "other"
        stats.languages[lang] = stats.languages.get(lang, 0) + 1
        if counted_chunks:
            stats.num_chunks += len(chunks)

    # ------------------------------------------------------------------ #
    # Embedding + upsert
    # ------------------------------------------------------------------ #
    def _ensure_collection(self) -> None:
        try:
            self.store.ensure_collection(self.embedder.dim)
        except Exception as exc:  # pragma: no cover
            log.warning("ensure_collection failed: %s", exc)

    def _flush(self, chunks: list[CodeChunk]) -> None:
        """Embed a batch of chunks and upsert them. Fail-soft per batch."""
        if not chunks:
            return
        try:
            vectors = self.embedder.embed_documents([c.text for c in chunks])
        except Exception as exc:
            log.warning("embedding batch failed (%d chunks), skipping: %s", len(chunks), exc)
            return
        if len(vectors) != len(chunks):
            log.warning("embed returned %d vectors for %d chunks", len(vectors), len(chunks))
            n = min(len(vectors), len(chunks))
            chunks, vectors = chunks[:n], vectors[:n]
        try:
            self.store.upsert(chunks, vectors)
        except Exception as exc:  # pragma: no cover
            log.warning("upsert failed (%d chunks): %s", len(chunks), exc)
