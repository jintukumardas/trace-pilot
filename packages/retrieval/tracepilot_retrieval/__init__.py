"""tracepilot_retrieval — ingestion, embeddings, Qdrant, and hybrid search.

Public surface (see docs/INTERNAL_CONTRACTS.md):

    get_embedder(settings=None) -> Embedder            # cached singleton
    get_qdrant_store(settings=None) -> QdrantStore     # cached singleton
    Retriever(store, embedder, settings)               # dense/sparse/hybrid + rerank
    Ingestor(store, embedder, settings)                # clone/open -> chunk -> embed -> upsert
    build_citations(evidence, max_snippet_lines=22)    # Evidence -> Citation
    pack_context(evidence, max_chars=16000)            # Evidence -> prompt-ready text

Heavy backends (fastembed, tree-sitter) are imported lazily inside their modules,
so importing this package never requires a model or grammar to be present.
"""

from __future__ import annotations

from .citations import build_citations, pack_context
from .embeddings import Embedder, FastEmbedEmbedder, OllamaEmbedder, get_embedder
from .ingest import Ingestor
from .qdrant_store import QdrantStore, get_qdrant_store
from .retriever import Retriever

__version__ = "0.1.0"

__all__ = [
    "get_embedder",
    "get_qdrant_store",
    "Retriever",
    "Ingestor",
    "build_citations",
    "pack_context",
    # secondary exports (useful for typing / direct use)
    "Embedder",
    "FastEmbedEmbedder",
    "OllamaEmbedder",
    "QdrantStore",
    "__version__",
]
