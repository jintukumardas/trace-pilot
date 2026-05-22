"""Qdrant-backed vector store: collection lifecycle, upsert, filtered search.

Wraps ``qdrant_client.QdrantClient``. All public methods are resilient to an
absent/empty collection (they return empty results rather than raising) so the
rest of the system degrades gracefully before anything is indexed.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    ChunkMetadata,
    ChunkType,
    CodeChunk,
    Evidence,
    RetrievalFilter,
)

log = get_logger("retrieval.qdrant")

# Payload fields we keep an index on for fast filtering.
_INDEXED_FIELDS = ("repository_id", "branch", "chunk_type", "language", "file_path")

# Deterministic namespace so the same chunk id always maps to the same point id.
_POINT_NAMESPACE = uuid.UUID("6f1d8a4e-2c3b-4a7e-9f10-2b6c5d4e3a21")


def _point_id(chunk_id: str) -> str:
    """Stable UUID point id derived from the chunk id (Qdrant requires int/UUID)."""
    return str(uuid.uuid5(_POINT_NAMESPACE, chunk_id))


class QdrantStore:
    """Thin, defensive wrapper around a single Qdrant collection."""

    def __init__(self, settings: Settings | None = None, client: QdrantClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.collection = self.settings.qdrant_collection
        if client is not None:
            self.client = client
        else:
            self.client = QdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key or None,
                timeout=30,
            )

    # ------------------------------------------------------------------ #
    # Collection lifecycle
    # ------------------------------------------------------------------ #
    def _exists(self) -> bool:
        try:
            return bool(self.client.collection_exists(self.collection))
        except Exception:  # pragma: no cover - older client
            try:
                self.client.get_collection(self.collection)
                return True
            except Exception:
                return False

    def ensure_collection(self, dim: int) -> None:
        """Create the collection (COSINE) + payload indexes if it does not exist."""
        try:
            if self._exists():
                self._ensure_payload_indexes()
                return
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(size=int(dim), distance=qm.Distance.COSINE),
            )
            log.info("created Qdrant collection %s (dim=%d, cosine)", self.collection, dim)
            self._ensure_payload_indexes()
        except Exception as exc:
            log.warning("ensure_collection failed for %s: %s", self.collection, exc)

    def _ensure_payload_indexes(self) -> None:
        for field in _INDEXED_FIELDS:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                # Already exists or transient; safe to ignore.
                pass

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def upsert(self, chunks: list[CodeChunk], vectors: list[list[float]]) -> None:
        """Upsert chunks + their vectors as Qdrant points keyed by deterministic UUID."""
        if not chunks:
            return
        if len(chunks) != len(vectors):
            log.warning("upsert size mismatch: %d chunks vs %d vectors", len(chunks), len(vectors))
        points: list[qm.PointStruct] = []
        for chunk, vector in zip(chunks, vectors):
            points.append(
                qm.PointStruct(
                    id=_point_id(chunk.id),
                    vector=[float(x) for x in vector],
                    payload=self._payload(chunk),
                )
            )
        if not points:
            return
        # Upsert in modest batches so a huge repo doesn't build one giant request.
        for start in range(0, len(points), 256):
            batch = points[start : start + 256]
            try:
                self.client.upsert(collection_name=self.collection, points=batch, wait=True)
            except Exception as exc:
                log.warning("upsert batch failed (%d points): %s", len(batch), exc)

    @staticmethod
    def _payload(chunk: CodeChunk) -> dict[str, Any]:
        md = chunk.metadata
        return {
            "chunk_id": chunk.id,
            "text": chunk.text,
            "content_hash": chunk.content_hash,
            "token_estimate": chunk.token_estimate,
            "repository_id": md.repository_id,
            "repo_name": md.repo_name,
            "branch": md.branch,
            "file_path": md.file_path,
            "language": md.language,
            "chunk_type": str(md.chunk_type),
            "symbol": md.symbol,
            "start_line": md.start_line,
            "end_line": md.end_line,
            "commit_hash": md.commit_hash,
        }

    # ------------------------------------------------------------------ #
    # Filters
    # ------------------------------------------------------------------ #
    def _build_filter(self, flt: RetrievalFilter | None) -> qm.Filter | None:
        if flt is None:
            return None
        must: list[qm.FieldCondition] = []
        if flt.repository_ids:
            must.append(
                qm.FieldCondition(
                    key="repository_id",
                    match=qm.MatchAny(any=list(flt.repository_ids)),
                )
            )
        if flt.branch:
            must.append(qm.FieldCondition(key="branch", match=qm.MatchValue(value=flt.branch)))
        if flt.file_types:
            # Map extensions to languages where possible; also keep raw ext match loose
            # by matching language values (callers pass logical types like 'py'/'ts').
            from .constants import LANG_BY_EXT

            langs = sorted({LANG_BY_EXT.get(ext.lower().lstrip("."), ext.lower()) for ext in flt.file_types})
            must.append(qm.FieldCondition(key="language", match=qm.MatchAny(any=langs)))
        if flt.chunk_types:
            must.append(
                qm.FieldCondition(
                    key="chunk_type",
                    match=qm.MatchAny(any=[str(ct) for ct in flt.chunk_types]),
                )
            )
        if flt.path_prefix:
            # Qdrant has no native prefix match on keyword; use full-text-ish match on
            # the path. We keep this as a text match which Qdrant supports on indexed
            # keyword fields via MatchText when available, else skip (filtered post-hoc).
            try:
                must.append(qm.FieldCondition(key="file_path", match=qm.MatchText(text=flt.path_prefix)))
            except Exception:  # pragma: no cover
                pass
        if not must:
            return None
        return qm.Filter(must=must)

    @staticmethod
    def _path_prefix_ok(payload: dict[str, Any], prefix: str | None) -> bool:
        if not prefix:
            return True
        return str(payload.get("file_path", "")).startswith(prefix)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    @staticmethod
    def _metadata_from_payload(payload: dict[str, Any]) -> ChunkMetadata:
        raw_type = payload.get("chunk_type", "unknown")
        try:
            chunk_type = ChunkType(raw_type)
        except ValueError:
            chunk_type = ChunkType.UNKNOWN
        return ChunkMetadata(
            repository_id=payload.get("repository_id", ""),
            repo_name=payload.get("repo_name", ""),
            branch=payload.get("branch", "main"),
            file_path=payload.get("file_path", ""),
            language=payload.get("language"),
            chunk_type=chunk_type,
            symbol=payload.get("symbol"),
            start_line=int(payload.get("start_line", 1) or 1),
            end_line=int(payload.get("end_line", 1) or 1),
            commit_hash=payload.get("commit_hash"),
        )

    def search(self, vector: list[float], flt: RetrievalFilter, top_k: int) -> list[Evidence]:
        """Dense ANN search returning ranked ``Evidence``. Empty list if nothing/empty."""
        if not self._exists():
            return []
        q_filter = self._build_filter(flt)
        # Over-fetch a little so post-hoc path-prefix filtering still yields top_k.
        fetch = max(int(top_k), 1)
        if flt and flt.path_prefix:
            fetch = fetch * 3
        try:
            hits = self.client.search(
                collection_name=self.collection,
                query_vector=[float(x) for x in vector],
                query_filter=q_filter,
                limit=fetch,
                with_payload=True,
            )
        except Exception as exc:
            log.warning("qdrant search failed: %s", exc)
            return []
        out: list[Evidence] = []
        prefix = flt.path_prefix if flt else None
        for rank, hit in enumerate(hits):
            payload = hit.payload or {}
            if not self._path_prefix_ok(payload, prefix):
                continue
            out.append(
                Evidence(
                    id=str(payload.get("chunk_id") or hit.id),
                    text=payload.get("text", ""),
                    score=float(hit.score),
                    metadata=self._metadata_from_payload(payload),
                    rank=rank,
                    retriever="dense",
                )
            )
            if len(out) >= top_k:
                break
        return out

    def iter_chunks(self, flt: RetrievalFilter, limit: int = 2000) -> list[Evidence]:
        """Scroll up to ``limit`` chunks (payload only) for BM25/sparse retrieval."""
        if not self._exists():
            return []
        q_filter = self._build_filter(flt)
        prefix = flt.path_prefix if flt else None
        out: list[Evidence] = []
        offset = None
        page = min(512, max(1, limit))
        try:
            while len(out) < limit:
                records, offset = self.client.scroll(
                    collection_name=self.collection,
                    scroll_filter=q_filter,
                    limit=page,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                if not records:
                    break
                for rec in records:
                    payload = rec.payload or {}
                    if not self._path_prefix_ok(payload, prefix):
                        continue
                    out.append(
                        Evidence(
                            id=str(payload.get("chunk_id") or rec.id),
                            text=payload.get("text", ""),
                            score=0.0,
                            metadata=self._metadata_from_payload(payload),
                            rank=0,
                            retriever="sparse",
                        )
                    )
                    if len(out) >= limit:
                        break
                if offset is None:
                    break
        except Exception as exc:
            log.warning("qdrant scroll failed: %s", exc)
        return out

    def delete_repository(self, repository_id: str) -> None:
        """Delete every point belonging to a repository (by payload filter)."""
        if not self._exists():
            return
        try:
            self.client.delete(
                collection_name=self.collection,
                points_selector=qm.FilterSelector(
                    filter=qm.Filter(
                        must=[
                            qm.FieldCondition(
                                key="repository_id",
                                match=qm.MatchValue(value=repository_id),
                            )
                        ]
                    )
                ),
                wait=True,
            )
            log.info("deleted points for repository %s", repository_id)
        except Exception as exc:
            log.warning("delete_repository failed for %s: %s", repository_id, exc)

    def count(self, repository_id: str | None = None) -> int:
        """Number of points, optionally scoped to one repository."""
        if not self._exists():
            return 0
        q_filter = None
        if repository_id:
            q_filter = qm.Filter(
                must=[qm.FieldCondition(key="repository_id", match=qm.MatchValue(value=repository_id))]
            )
        try:
            result = self.client.count(collection_name=self.collection, count_filter=q_filter, exact=True)
            return int(result.count)
        except Exception as exc:
            log.warning("qdrant count failed: %s", exc)
            return 0

    def content_hashes(self, repository_id: str) -> dict[str, str]:
        """Return ``{file_path: content_hash}`` for incremental indexing.

        When a file has multiple chunks we keep the first hash seen; the ingestor
        only needs a stable per-file marker, and any change to the file changes at
        least one chunk hash, which the ingestor treats as "re-index the file".
        """
        if not self._exists():
            return {}
        out: dict[str, str] = {}
        q_filter = qm.Filter(
            must=[qm.FieldCondition(key="repository_id", match=qm.MatchValue(value=repository_id))]
        )
        offset = None
        try:
            while True:
                records, offset = self.client.scroll(
                    collection_name=self.collection,
                    scroll_filter=q_filter,
                    limit=512,
                    offset=offset,
                    with_payload=["file_path", "content_hash"],
                    with_vectors=False,
                )
                if not records:
                    break
                for rec in records:
                    payload = rec.payload or {}
                    path = payload.get("file_path")
                    chash = payload.get("content_hash")
                    if path and chash and path not in out:
                        out[path] = chash
                if offset is None:
                    break
        except Exception as exc:
            log.warning("content_hashes scroll failed for %s: %s", repository_id, exc)
        return out


# --------------------------------------------------------------------------- #
# Cached factory
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4)
def _cached_store(qdrant_url: str, collection: str) -> QdrantStore:
    return QdrantStore()


def get_qdrant_store(settings: Settings | None = None) -> QdrantStore:
    """Return a cached ``QdrantStore`` singleton keyed by (url, collection)."""
    s = settings or get_settings()
    return _cached_store(s.qdrant_url, s.qdrant_collection)
