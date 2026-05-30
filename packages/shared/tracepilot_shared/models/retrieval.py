"""Retrieval, chunk and citation models — the shared shape of all evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .common import ChunkType

RetrievalStrategy = Literal["dense", "sparse", "hybrid"]


class RetrievalFilter(BaseModel):
    """Metadata filters applied to a retrieval query (maps to Qdrant payload filters)."""

    repository_ids: list[str] | None = None
    branch: str | None = None
    file_types: list[str] | None = Field(default=None, description="File extensions e.g. ['py', 'ts']")
    path_prefix: str | None = None
    chunk_types: list[ChunkType] | None = None


class RetrievalQuery(BaseModel):
    """A single retrieval request against the vector store."""

    query: str
    top_k: int = Field(default=8, ge=1, le=50)
    strategy: RetrievalStrategy = "hybrid"
    rerank: bool = False
    filter: RetrievalFilter = Field(default_factory=RetrievalFilter)


class ChunkMetadata(BaseModel):
    """Rich metadata stored alongside every vector point. Drives filtering + citations."""

    repository_id: str
    repo_name: str
    branch: str = "main"
    file_path: str
    language: str | None = None
    chunk_type: ChunkType = ChunkType.UNKNOWN
    symbol: str | None = Field(default=None, description="Class/function/symbol name if detected")
    start_line: int = 1
    end_line: int = 1
    commit_hash: str | None = None


class CodeChunk(BaseModel):
    """A chunk produced by the ingestion pipeline, ready to embed."""

    id: str
    text: str
    metadata: ChunkMetadata
    content_hash: str = Field(default="", description="Hash of text, used for incremental indexing")
    token_estimate: int = 0


class Evidence(BaseModel):
    """A retrieved chunk with score and provenance. The atomic unit of grounding."""

    id: str
    text: str
    score: float = Field(..., description="Fused relevance score, higher is better")
    metadata: ChunkMetadata
    rank: int = 0
    retriever: RetrievalStrategy = "hybrid"


class Citation(BaseModel):
    """A user-facing citation derived from an Evidence block."""

    index: int = Field(..., description="1-based marker referenced in the answer, e.g. [1]")
    repository: str
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    score: float = 0.0


class RetrievalResult(BaseModel):
    """The full output of a retrieval call."""

    query: str
    strategy: RetrievalStrategy
    evidence: list[Evidence] = Field(default_factory=list)
    latency_ms: float = 0.0
    reranked: bool = False
